#!/usr/bin/env python3
"""
CLI benchmarking harness for an AWS Lambda MicroVM hosting a RAG pipeline.
This tool tests ingest and query endpoints under both "warm" and "suspended" states.
"""

import argparse
import subprocess
import requests
import json
import csv
import time
import os
import glob
import sys
import datetime

def print_progress(*args, **kwargs):
    """Prints progress messages to stderr."""
    kwargs['file'] = sys.stderr
    print(*args, **kwargs)

def load_env_defaults(args):
    """
    If --microvm-id or --endpoint are not provided, read them from ../main/.env
    relative to this script's directory.
    """
    if args.microvm_id and args.endpoint:
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, '..', 'main', '.env')
    
    if not os.path.exists(env_path):
        print_progress(f"Warning: .env file not found at {env_path}")
        return

    try:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('MICROVM_URL='):
                    if not args.endpoint:
                        args.endpoint = line.split('=', 1)[1]
                elif line.startswith('MICROVM_ID='):
                    if not args.microvm_id:
                        args.microvm_id = line.split('=', 1)[1]
    except Exception as e:
        print_progress(f"Error reading .env file: {e}")

def get_auth_token(region, microvm_id):
    """
    Gets a JWE token for authenticating requests to the MicroVM using the AWS CLI.
    """
    cmd = [
        "aws", "lambda-microvms", "create-microvm-auth-token",
        "--microvm-identifier", microvm_id,
        "--expiration-in-minutes", "30",
        "--allowed-ports", '[{"allPorts":{}}]',
        "--region", region
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    response = json.loads(result.stdout)
    return response['authToken']['X-aws-proxy-auth']

def suspend_and_wait(region, microvm_id):
    """
    Suspends the MicroVM and polls until the state is SUSPENDED.
    Times out after 120 seconds.
    """
    print_progress(f"Suspending microvm {microvm_id}...")
    subprocess.run([
        "aws", "lambda-microvms", "suspend-microvm",
        "--microvm-identifier", microvm_id,
        "--region", region
    ], capture_output=True, check=True)
    
    timeout_s = 120
    poll_interval_s = 2
    attempts = timeout_s // poll_interval_s
    
    for _ in range(attempts):
        result = subprocess.run([
            "aws", "lambda-microvms", "get-microvm",
            "--microvm-identifier", microvm_id,
            "--region", region
        ], capture_output=True, text=True, check=True)
        response = json.loads(result.stdout)
        state = response.get('state')
        if state == 'SUSPENDED':
            print_progress("MicroVM is now SUSPENDED. Waiting 5s before firing request...")
            time.sleep(5)
            return
        print_progress(f"Current state: {state}. Waiting...")
        time.sleep(poll_interval_s)
        
    raise TimeoutError(f"MicroVM did not suspend within {timeout_s} seconds.")

def ensure_running(region, microvm_id):
    """
    Ensures the MicroVM is ready for requests.
    If SUSPENDED, requests will trigger auto-resume. Otherwise it must be RUNNING.
    """
    result = subprocess.run([
        "aws", "lambda-microvms", "get-microvm",
        "--microvm-identifier", microvm_id,
        "--region", region
    ], capture_output=True, text=True, check=True)
    response = json.loads(result.stdout)
    state = response.get('state')
    if state == 'RUNNING':
        print_progress("MicroVM is RUNNING.")
    elif state == 'SUSPENDED':
        print_progress("MicroVM is SUSPENDED. First request will trigger auto-resume.")
    else:
        raise ValueError(f"Unexpected MicroVM state: {state}")

def do_ingest_request(endpoint, token, pdf_path):
    """
    Performs a multipart file upload to the /ingest endpoint.
    Returns timings and epoch timestamps for resume overhead computation.
    """
    start_ns = time.perf_counter_ns()
    url = endpoint.rstrip('/') + '/ingest'
    headers = {
        'X-aws-proxy-auth': token,
        'X-aws-proxy-port': '8080'
    }

    try:
        with open(pdf_path, 'rb') as f:
            files = {'file': (os.path.basename(pdf_path), f, 'application/pdf')}
            # Capture wall-clock send time immediately before the request leaves
            probe_send_epoch_ms = round(time.time() * 1000, 3)
            response = requests.post(url, headers=headers, files=files, timeout=300)

        response.raise_for_status()
        end_ns = time.perf_counter_ns()

        probe_ms = round((end_ns - start_ns) / 1_000_000, 3)
        json_resp = response.json()
        if 'timings' not in json_resp:
            raise ValueError(f"Missing 'timings' in response: {json_resp}")
        timings = json_resp['timings']
        server_entry_epoch_ms = json_resp.get('server_entry_epoch_ms', 0.0)
        resume_overhead_ms = round(server_entry_epoch_ms - probe_send_epoch_ms, 3)

        return {
            'probe_e2e_ms': probe_ms,
            'probe_send_epoch_ms': probe_send_epoch_ms,
            'server_entry_epoch_ms': server_entry_epoch_ms,
            'resume_overhead_ms': resume_overhead_ms,
            'server_total_ms': timings.get('server_total_ms', 0.0),
            'bedrock_ms': timings.get('bedrock_ms', 0.0),
            'file_read_ms': timings.get('file_read_ms', 0.0),
            'pdf_read_ms': timings.get('pdf_read_ms', 0.0),
            'chunking_ms': timings.get('chunking_ms', 0.0),
            'lance_table_open_ms': timings.get('lance_table_open_ms', 0.0),
            'lance_insert_rows_ms': timings.get('lance_insert_rows_ms', 0.0),
            # query-only fields — not present for ingest
            'lancedb_open_ms': 0.0,
            'lancedb_search_ms': 0.0,
            'vector_strip_ms': 0.0,
            'context_length_chars': 0,
            'error': ''
        }
    except Exception as e:
        end_ns = time.perf_counter_ns()
        probe_ms = round((end_ns - start_ns) / 1_000_000, 3)
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                err_body = e.response.json()
                if isinstance(err_body, dict):
                    detail = err_body.get('detail') or err_body.get('message')
                    if detail:
                        err_msg = f"{e.response.status_code} Error: {detail}"
            except Exception:
                err_msg = f"{e.response.status_code} Error: {e.response.text[:200]}"

        return {
            'probe_e2e_ms': probe_ms,
            'probe_send_epoch_ms': 0.0,
            'server_entry_epoch_ms': 0.0,
            'resume_overhead_ms': 0.0,
            'server_total_ms': 0.0,
            'bedrock_ms': 0.0,
            'file_read_ms': 0.0,
            'pdf_read_ms': 0.0,
            'chunking_ms': 0.0,
            'lance_table_open_ms': 0.0,
            'lance_insert_rows_ms': 0.0,
            'lancedb_open_ms': 0.0,
            'lancedb_search_ms': 0.0,
            'vector_strip_ms': 0.0,
            'context_length_chars': 0,
            'error': err_msg
        }

def do_query_request(endpoint, token, question):
    """
    Performs a JSON POST request to the /query endpoint.
    Returns timings and epoch timestamps for resume overhead computation.
    """
    start_ns = time.perf_counter_ns()
    url = endpoint.rstrip('/') + '/query'
    headers = {
        'X-aws-proxy-auth': token,
        'X-aws-proxy-port': '8080',
        'Content-Type': 'application/json'
    }

    try:
        # Capture wall-clock send time immediately before the request leaves
        probe_send_epoch_ms = round(time.time() * 1000, 3)
        response = requests.post(url, headers=headers, json={'question': question}, timeout=300)
        response.raise_for_status()
        end_ns = time.perf_counter_ns()

        probe_ms = round((end_ns - start_ns) / 1_000_000, 3)
        json_resp = response.json()
        if 'timings' not in json_resp:
            raise ValueError(f"Missing 'timings' in response: {json_resp}")
        timings = json_resp['timings']
        server_entry_epoch_ms = json_resp.get('server_entry_epoch_ms', 0.0)
        resume_overhead_ms = round(server_entry_epoch_ms - probe_send_epoch_ms, 3)

        return {
            'probe_e2e_ms': probe_ms,
            'probe_send_epoch_ms': probe_send_epoch_ms,
            'server_entry_epoch_ms': server_entry_epoch_ms,
            'resume_overhead_ms': resume_overhead_ms,
            'server_total_ms': timings.get('server_total_ms', 0.0),
            'bedrock_ms': timings.get('bedrock_ms', 0.0),
            'lancedb_open_ms': timings.get('lancedb_open_ms', 0.0),
            'lancedb_search_ms': timings.get('lancedb_search_ms', 0.0),
            'vector_strip_ms': timings.get('vector_strip_ms', 0.0),
            'context_length_chars': json_resp.get('context_length_chars', 0),
            # ingest-only fields — not present for query
            'file_read_ms': 0.0,
            'pdf_read_ms': 0.0,
            'chunking_ms': 0.0,
            'lance_table_open_ms': 0.0,
            'lance_insert_rows_ms': 0.0,
            'error': ''
        }
    except Exception as e:
        end_ns = time.perf_counter_ns()
        probe_ms = round((end_ns - start_ns) / 1_000_000, 3)
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                err_body = e.response.json()
                if isinstance(err_body, dict):
                    detail = err_body.get('detail') or err_body.get('message')
                    if detail:
                        err_msg = f"{e.response.status_code} Error: {detail}"
            except Exception:
                err_msg = f"{e.response.status_code} Error: {e.response.text[:200]}"

        return {
            'probe_e2e_ms': probe_ms,
            'probe_send_epoch_ms': 0.0,
            'server_entry_epoch_ms': 0.0,
            'resume_overhead_ms': 0.0,
            'server_total_ms': 0.0,
            'bedrock_ms': 0.0,
            'lancedb_open_ms': 0.0,
            'lancedb_search_ms': 0.0,
            'vector_strip_ms': 0.0,
            'file_read_ms': 0.0,
            'pdf_read_ms': 0.0,
            'chunking_ms': 0.0,
            'lance_table_open_ms': 0.0,
            'lance_insert_rows_ms': 0.0,
            'context_length_chars': 0,
            'error': err_msg
        }

def build_result_row(benchmark_type, item_name, metrics):
    """
    Formats the request metrics into a final dictionary row.
    resume_overhead_ms is already computed in do_ingest/query_request as
    server_entry_epoch_ms - probe_send_epoch_ms, so we read it directly here.
    """
    return {
        'benchmark_type': benchmark_type,
        'item_name_or_query': item_name,
        'probe_e2e_ms': metrics.get('probe_e2e_ms', 0.0),
        'probe_send_epoch_ms': metrics.get('probe_send_epoch_ms', 0.0),
        'server_entry_epoch_ms': metrics.get('server_entry_epoch_ms', 0.0),
        'resume_overhead_ms': metrics.get('resume_overhead_ms', 0.0),
        'server_total_ms': metrics.get('server_total_ms', 0.0),
        'bedrock_ms': metrics.get('bedrock_ms', 0.0),
        # ingest-specific
        'file_read_ms': metrics.get('file_read_ms', 0.0),
        'pdf_read_ms': metrics.get('pdf_read_ms', 0.0),
        'chunking_ms': metrics.get('chunking_ms', 0.0),
        'lance_table_open_ms': metrics.get('lance_table_open_ms', 0.0),
        'lance_insert_rows_ms': metrics.get('lance_insert_rows_ms', 0.0),
        # query-specific
        'lancedb_open_ms': metrics.get('lancedb_open_ms', 0.0),
        'lancedb_search_ms': metrics.get('lancedb_search_ms', 0.0),
        'vector_strip_ms': metrics.get('vector_strip_ms', 0.0),
        'context_length_chars': metrics.get('context_length_chars', 0),
        'error': metrics.get('error', '')
    }

def run_ingest_suspended(args, region, endpoint, microvm_id):
    """
    Benchmarks ingestion with the MicroVM suspended before each request.
    """
    results = []
    pdf_files = sorted(glob.glob(os.path.join(args.dir, '*.pdf')))
    if not pdf_files:
        print_progress(f"No PDF files found in directory: {args.dir}")
        return results

    for i, pdf_path in enumerate(pdf_files):
        print_progress(f"Processing [{i+1}/{len(pdf_files)}]: {os.path.basename(pdf_path)}")
        suspend_and_wait(region, microvm_id)
        token = get_auth_token(region, microvm_id)
        metrics = do_ingest_request(endpoint, token, pdf_path)
        row = build_result_row('ingest-suspended', os.path.basename(pdf_path), metrics)
        if row['error']:
            print_progress(f"  Error: {row['error']}")
        results.append(row)
        
    return results

def run_ingest_warm(args, region, endpoint, microvm_id):
    """
    Benchmarks ingestion on a continuously running (warm) MicroVM.
    """
    results = []
    pdf_files = sorted(glob.glob(os.path.join(args.dir, '*.pdf')))
    if not pdf_files:
        print_progress(f"No PDF files found in directory: {args.dir}")
        return results

    ensure_running(region, microvm_id)
    token = get_auth_token(region, microvm_id)
    
    for i, pdf_path in enumerate(pdf_files):
        print_progress(f"Processing [{i+1}/{len(pdf_files)}]: {os.path.basename(pdf_path)}")
        metrics = do_ingest_request(endpoint, token, pdf_path)
        row = build_result_row('ingest-warm', os.path.basename(pdf_path), metrics)
        if row['error']:
            print_progress(f"  Error: {row['error']}")
        results.append(row)
        
    return results

def run_query_suspended(args, region, endpoint, microvm_id):
    """
    Benchmarks querying with the MicroVM suspended before each request.
    """
    results = []
    try:
        with open(args.questions, 'r') as f:
            questions = json.load(f)
    except Exception as e:
        print_progress(f"Failed to load questions from {args.questions}: {e}")
        return results
        
    for i, question in enumerate(questions):
        print_progress(f"Query [{i+1}/{len(questions)}]: {question[:50]}...")
        suspend_and_wait(region, microvm_id)
        token = get_auth_token(region, microvm_id)
        metrics = do_query_request(endpoint, token, question)
        row = build_result_row('query-suspended', question, metrics)
        if row['error']:
            print_progress(f"  Error: {row['error']}")
        results.append(row)
        
    return results

def run_query_warm(args, region, endpoint, microvm_id):
    """
    Benchmarks querying on a continuously running (warm) MicroVM.
    """
    results = []
    try:
        with open(args.questions, 'r') as f:
            questions = json.load(f)
    except Exception as e:
        print_progress(f"Failed to load questions from {args.questions}: {e}")
        return results
        
    ensure_running(region, microvm_id)
    token = get_auth_token(region, microvm_id)
    
    for i, question in enumerate(questions):
        print_progress(f"Query [{i+1}/{len(questions)}]: {question[:50]}...")
        metrics = do_query_request(endpoint, token, question)
        row = build_result_row('query-warm', question, metrics)
        if row['error']:
            print_progress(f"  Error: {row['error']}")
        results.append(row)
        
    return results

def write_report(results, output_path):
    """
    Writes the benchmark results to a CSV file and a JSON file.
    """
    if not results:
        print_progress("No results to write.")
        return

    # Write CSV
    fieldnames = [
        'benchmark_type', 'item_name_or_query', 'probe_e2e_ms',
        'probe_send_epoch_ms', 'server_entry_epoch_ms', 'resume_overhead_ms',
        'server_total_ms', 'bedrock_ms',
        # ingest-specific
        'file_read_ms', 'pdf_read_ms', 'chunking_ms',
        'lance_table_open_ms', 'lance_insert_rows_ms',
        # query-specific
        'lancedb_open_ms', 'lancedb_search_ms', 'vector_strip_ms',
        # shared
        'bedrock_connection_creation_ms', 'context_length_chars', 'error'
    ]
    
    try:
        with open(output_path, 'w', newline='') as f:
            # restval=0 means any missing numeric field writes 0 instead of ''
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval=0, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(results)
        print_progress(f"Saved CSV report to {output_path}")
    except Exception as e:
        print_progress(f"Failed to write CSV: {e}")

    # Write JSON
    base, _ = os.path.splitext(output_path)
    json_path = f"{base}.json"
    try:
        with open(json_path, 'w') as f:
            json.dump(results, f, indent=2)
        print_progress(f"Saved JSON report to {json_path}")
    except Exception as e:
        print_progress(f"Failed to write JSON: {e}")

def main():
    parser = argparse.ArgumentParser(description="MicroVM RAG Benchmark Harness")
    parser.add_argument('--mode', required=True, choices=[
        'ingest-suspended', 'ingest-warm', 'query-suspended', 'query-warm'
    ], help="Benchmark mode to run.")
    parser.add_argument('--dir', help="Path to directory of PDF files (required for ingest modes).")
    parser.add_argument('--questions', help="Path to JSON file with array of queries (required for query modes).")
    parser.add_argument('--microvm-id', help="Target MicroVM ID (defaults to reading ../main/.env).")
    parser.add_argument('--endpoint', help="Target MicroVM URL (defaults to reading ../main/.env).")
    parser.add_argument('--region', default='us-east-1', help="AWS Region (default: us-east-1).")
    parser.add_argument('--output', help="Optional output CSV path override. By default, auto-generates in results/ directory.")
    
    args = parser.parse_args()
    
    if not args.output:
        timestamp = datetime.datetime.now().strftime("%Y:%m:%d:%H:%M:%S")
        os.makedirs("results", exist_ok=True)
        args.output = f"results/{args.mode}_{timestamp}.csv"
    # Validation
    if 'ingest' in args.mode and not args.dir:
        parser.error("--dir is required for ingest modes.")
    if 'query' in args.mode and not args.questions:
        parser.error("--questions is required for query modes.")

    # Environment defaults
    load_env_defaults(args)
    if not args.microvm_id or not args.endpoint:
        parser.error("Failed to resolve --microvm-id or --endpoint. Provide them explicitly or ensure ../main/.env exists.")

    print_progress(f"Mode: {args.mode}")
    print_progress(f"MicroVM ID: {args.microvm_id}")
    print_progress(f"Endpoint: {args.endpoint}")
    print_progress(f"Region: {args.region}")

    region = args.region

    # Dispatch to appropriate mode handler
    results = []
    if args.mode == 'ingest-suspended':
        results = run_ingest_suspended(args, region, args.endpoint, args.microvm_id)
    elif args.mode == 'ingest-warm':
        results = run_ingest_warm(args, region, args.endpoint, args.microvm_id)
    elif args.mode == 'query-suspended':
        results = run_query_suspended(args, region, args.endpoint, args.microvm_id)
    elif args.mode == 'query-warm':
        results = run_query_warm(args, region, args.endpoint, args.microvm_id)

    write_report(results, args.output)

    # Print summary
    if results:
        successful_probes = [r['probe_e2e_ms'] for r in results if not r['error']]
        print_progress("\n--- Benchmark Summary ---")
        print_progress(f"Total items processed: {len(results)}")
        print_progress(f"Errors: {len(results) - len(successful_probes)}")
        if successful_probes:
            print_progress(f"Mean probe_e2e_ms: {sum(successful_probes) / len(successful_probes):.3f}")
            print_progress(f"Min probe_e2e_ms: {min(successful_probes):.3f}")
            print_progress(f"Max probe_e2e_ms: {max(successful_probes):.3f}")

if __name__ == '__main__':
    main()
