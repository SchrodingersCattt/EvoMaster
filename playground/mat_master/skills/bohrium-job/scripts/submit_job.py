"""submit_job.py - submit a Bohrium job and return job_id immediately.

This script performs only the 3 submission steps:
1) /openapi/v1/job/create
2) upload input.zip to Tiefblue
3) /openapi/v2/job/add
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except ImportError:
    pass

ACCESS_KEY = os.environ.get('BOHRIUM_ACCESS_KEY', '').strip()
PROJECT_ID = int(os.environ.get('BOHRIUM_PROJECT_ID', '-1') or '-1')
_ENV = os.environ.get('SERVICE_ENV', '').strip()
_URL_PART = f'.{_ENV}' if _ENV and _ENV not in ('prod', 'production') else ''
_DEFAULT_BASE = f'https://openapi{_URL_PART}.dp.tech' if _URL_PART else 'https://open.bohrium.com'
OPENAPI_BASE = os.environ.get('BOHRIUM_BASE_URL', _DEFAULT_BASE).rstrip('/')

_AUTH_HEADER = {'accessKey': ACCESS_KEY, 'Content-Type': 'application/json'}


def _post(path: str, payload: dict, timeout: int = 30) -> dict:
    response = requests.post(
        f"{OPENAPI_BASE}{path}",
        headers=_AUTH_HEADER,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _step1_create(job_name: str) -> dict:
    response = _post(
        '/openapi/v1/job/create',
        {'projectId': PROJECT_ID, 'jobName': job_name},
    )
    if response.get('code') != 0:
        raise RuntimeError(f"job/create failed: {response}")
    return response['data']


def _step2_upload(create_data: dict, zip_path: Path) -> str:
    try:
        from bohrium_open_sdk.opensdk._tiefblue_client import (
            Tiefblue as _TiefblueClient,
        )
    except ImportError as exc:
        raise RuntimeError(
            'bohrium SDK not installed - run: pip install bohrium_open_sdk'
        ) from exc

    store_path = create_data['storePath']
    store_host = create_data['storeHost'].rstrip('/')
    token = create_data['token']
    oss_key = store_path + 'input.zip'

    # Tiefblue 网关要求 Authorization: Bearer <token>，否则 401 ErrGatewayTokenInvalid
    tf_client = _TiefblueClient(base_url=store_host)
    resp = tf_client.upload_from_file_multi_part(
        object_key=oss_key,
        file_path=str(zip_path),
        custom_headers={'Authorization': f"Bearer {token}"},
        progress_bar=False,
    )
    if isinstance(resp, dict) and resp.get('code') not in (0, None):
        raise RuntimeError(f"tiefblue upload failed: {resp}")
    return oss_key


def _step3_add(
    oss_key: str,
    job_name: str,
    image: str,
    cmd: str,
    machine: str,
    disk_size: int,
) -> dict:
    payload = {
        'projectId': PROJECT_ID,
        'jobName': job_name,
        'jobType': 'indicate',
        'scassType': machine,
        'cmd': cmd,
        'imageName': image,
        'ossPath': [oss_key],
        'inputFileMethod': 1,
        'inputFileType': 3,
        'diskSize': disk_size,
        'logFiles': ['log'],
    }
    response = _post('/openapi/v2/job/add', payload)
    if response.get('code') != 0:
        raise RuntimeError(f"job/add failed: {response}")
    return response['data']


def _zip_directory(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in src_dir.rglob('*'):
            if file_path.is_file():
                zip_file.write(file_path, file_path.relative_to(src_dir))


def submit_job(
    input_dir: Path,
    image: str,
    cmd: str,
    machine: str,
    job_name: str,
    disk_size: int,
) -> tuple[int, int]:
    with tempfile.TemporaryDirectory(prefix='bsm_submit_') as tmp_dir:
        zip_path = Path(tmp_dir) / 'input.zip'
        _zip_directory(input_dir, zip_path)

        create_data = _step1_create(job_name)
        oss_key = _step2_upload(create_data, zip_path)
        add_data = _step3_add(oss_key, job_name, image, cmd, machine, disk_size)

    job_id = int(add_data['jobId'])
    bohr_job_id = int(add_data.get('bohrJobId') or add_data['jobId'])
    return job_id, bohr_job_id


def main() -> None:
    parser = argparse.ArgumentParser(description='Submit Bohrium job and return job_id')
    parser.add_argument('--input-dir', required=True, help='Input directory to upload')
    parser.add_argument('--image', required=True, help='Docker image for the job')
    parser.add_argument(
        '--cmd', required=True, help='Shell command to run inside container'
    )
    parser.add_argument(
        '--machine', default='c32_m128_cpu', help='Bohrium machine type'
    )
    parser.add_argument('--job-name', default=None, help='Human-readable job name')
    parser.add_argument(
        '--software', default='unknown', help='Software label for default job name'
    )
    parser.add_argument('--disk-size', type=int, default=50, help='Disk size in GB')

    args = parser.parse_args()

    if not ACCESS_KEY:
        print(json.dumps({'success': False, 'error': 'BOHRIUM_ACCESS_KEY not set'}))
        sys.exit(1)
    if PROJECT_ID <= 0:
        print(
            json.dumps(
                {'success': False, 'error': 'BOHRIUM_PROJECT_ID not set or invalid'}
            )
        )
        sys.exit(1)

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(
            json.dumps(
                {'success': False, 'error': f"input_dir not found: {args.input_dir}"}
            )
        )
        sys.exit(1)

    job_name = args.job_name or f"matmaster-{args.software}-job"

    try:
        job_id, bohr_job_id = submit_job(
            input_dir=input_dir,
            image=args.image,
            cmd=args.cmd,
            machine=args.machine,
            job_name=job_name,
            disk_size=args.disk_size,
        )
    except Exception as exc:
        print(
            json.dumps(
                {'success': False, 'error': f"submit failed: {exc}"}, ensure_ascii=False
            )
        )
        sys.exit(1)

    print(
        json.dumps(
            {
                'success': True,
                'job_id': job_id,
                'bohr_job_id': bohr_job_id,
                'status': 'Submitted',
            },
            ensure_ascii=False,
        )
    )


if __name__ == '__main__':
    main()
