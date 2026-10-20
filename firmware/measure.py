"""Summarize adapter logs. Callback rate is not display Hz or end-to-end latency."""
import argparse
import json
from pathlib import Path
import re
import statistics


def summarize(text):
    text=text.replace('\0','')
    sessions=[]
    samples=[]
    for line in text.splitlines():
        if re.search(r'Real3D CPU init|CPU v7: LUT initialized',line):
            if samples: sessions.append(samples)
            samples=[]
        match=re.search(r'frame=(\d+).*time_us=(\d+)/(\d+)/(\d+) stamp_us=(\d+)',line)
        if match:
            frame,mapping,compute,flush,stamp=map(int,match.groups())
            samples.append(dict(frame=frame,stamp=stamp,map_us=mapping,
                                compute_us=compute,flush_us=flush))
        match=re.search(r'frame timestamp microseconds .*size=(\d+) value=(\d+)',line)
        if match:
            frame,stamp=map(int,match.groups())
            samples.append(dict(frame=frame,stamp=stamp))
    if samples:sessions.append(samples)
    result=[]
    for session in sessions:
        intervals=[(b['frame']-a['frame'])*1e6/(b['stamp']-a['stamp'])
                   for a,b in zip(session,session[1:])
                   if b['stamp']>a['stamp'] and b['frame']>a['frame']]
        report={'samples':len(session),'interval_callback_rates':intervals}
        if intervals:
            report.update(min_callback_fps=min(intervals),
                          median_callback_fps=statistics.median(intervals))
        for field in ('map_us','compute_us','flush_us'):
            values=[s[field] for s in session if field in s]
            if values:report[field]={'median':statistics.median(values),'max':max(values)}
        result.append(report)
    return {'sessions':result,
            'limitations':'Sampled adapter callbacks only; display refresh, NPU queueing, '
                          'end-to-end latency and stereo quality require separate measurements.',
            'stable_60_fps_confirmed':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    encoded=json.dumps(summarize(args.log.read_text(errors='replace')),indent=2)
    if args.output:args.output.write_text(encoded+'\n',encoding='utf-8')
    print(encoded)
