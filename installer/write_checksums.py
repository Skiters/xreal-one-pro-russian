"""Write release checksums without depending on PowerShell module availability."""
import hashlib
from pathlib import Path
import sys


def write_checksums(folder):
    folder=Path(folder)
    lines=[]
    for path in sorted(folder.iterdir(),key=lambda p:p.name):
        if not path.is_file() or path.name=='SHA256SUMS.txt':
            continue
        with path.open('rb') as handle:
            digest=hashlib.file_digest(handle,'sha256').hexdigest()
        lines.append(digest+'  '+path.name+'\n')
    (folder/'SHA256SUMS.txt').write_text(''.join(lines),encoding='utf-8',newline='\n')


if __name__=='__main__':
    write_checksums(sys.argv[1])
