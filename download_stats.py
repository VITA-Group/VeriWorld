#!/usr/bin/env python3
"""Show GitHub Release asset download counters; never add split-part counts."""
import argparse
import json
import subprocess
import urllib.parse
import urllib.request

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--gh', action='store_true', help='Use logged-in gh (required for private releases)')
    args = p.parse_args()
    endpoint = f'repos/{args.repo}/releases/tags/{urllib.parse.quote(args.tag, safe="")}'
    if args.gh:
        result = subprocess.run(['gh', 'api', endpoint], check=True, capture_output=True, text=True)
        release = json.loads(result.stdout)
    else:
        req = urllib.request.Request('https://api.github.com/' + endpoint,
                                     headers={'User-Agent': 'VeriWorld-download-statistics/1'})
        with urllib.request.urlopen(req, timeout=30) as response:
            release = json.load(response)
    print('Asset\tBytes\tDownloads')
    for asset in release['assets']:
        print(f'{asset["name"]}\t{asset["size"]}\t{asset["download_count"]}')
    print('\nCounts are per asset request, not unique people or verified complete installations.')
    print('Do not sum the counts of an archive\'s parts as full-package downloads.')

if __name__ == '__main__':
    main()
