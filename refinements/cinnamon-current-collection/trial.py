#!/usr/bin/python3
"""Serial, automatically restored live verification of collection profiles."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
PROFILES=json.loads((ROOT/'profiles.json').read_text())['profiles']

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profiles',nargs='+',choices=list(PROFILES))
    parser.add_argument('--probes',nargs='+',choices=('shell','apps','widgets','extra','quality','cava','gtile','library','inheritance','gtk2'),default=['shell','apps','widgets'])
    args=parser.parse_args()
    batch=ROOT/'verification/trials'/time.strftime('%Y%m%d-%H%M%S');batch.mkdir(parents=True)
    results=[]
    for slug in args.profiles:
        out=batch/slug;out.mkdir()
        report={'profile':slug,'status':'failed','probes':{}}
        def command(name,argv,timeout):
            process=subprocess.Popen(['/usr/bin/python3',*map(str,argv)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            expired=False
            try:stdout,stderr=process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                expired=True;process.terminate()
                try:stdout,stderr=process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();stdout,stderr=process.communicate()
            (out/(name+'.stdout')).write_text(stdout);(out/(name+'.stderr')).write_text(stderr)
            if expired:raise RuntimeError(name+' exceeded its runtime limit; output saved')
            if process.returncode:raise RuntimeError(name+' failed: '+(stderr or stdout)[-1800:])
            return stdout
        receipt=None
        try:
            applied=json.loads(command('apply',[ROOT/'live.py','apply',slug],60));receipt=applied['receipt']
            report['receipt']=receipt;report['skipped_activation']=applied['skipped']
            print(slug+': selected; running '+', '.join(args.probes),flush=True)
            probe_errors=[]
            for probe in args.probes:
                if probe=='shell':argv=[ROOT/'verify_live.py',slug];timeout=45
                elif probe=='apps':argv=[ROOT/'verify_live_apps.py',slug];timeout=110
                elif probe=='widgets':argv=[ROOT/'runtime-widgets/live-probe.py','--profile',slug,'--coordinator-live-run','--all-windows'];timeout=95
                elif probe=='extra':argv=[ROOT/'runtime-apps/verify_extra.py',slug,'--execute'];timeout=75
                elif probe=='quality':argv=[ROOT/'runtime-quality/verify_visual.py',slug];timeout=35
                elif probe=='cava':argv=[ROOT/'runtime-apps/verify_cava.py',slug,'--execute'];timeout=30
                elif probe=='gtile':argv=[ROOT/'verify_gtile.py',slug];timeout=25
                elif probe=='library':argv=[ROOT/'runtime-widgets/library-probe.py','--profile',slug,'--coordinator-live-run'];timeout=150
                elif probe=='inheritance':argv=[ROOT/'runtime-apps/verify_inheritance.py',slug,'--execute'];timeout=75
                elif probe=='gtk2':
                    argv=[ROOT.parents[1]/'presets/pastel-leather/completion/applications/gtk2_probe.py',
                          '--execute','--out',out/'gtk2-controls'];timeout=30
                try:
                    stdout=command(probe,argv,timeout)
                    report['probes'][probe]=json.loads(stdout)
                    print(slug+': '+probe+' passed',flush=True)
                except Exception as exc:
                    # A completed helper can report a failed observation while
                    # still cleaning up its own windows. Keep independent
                    # observations in the same trial; never hide the failure.
                    raw=(out/(probe+'.stdout')).read_text() if (out/(probe+'.stdout')).exists() else ''
                    try:detail=json.loads(raw)
                    except (ValueError,TypeError):raise RuntimeError(str(exc)) from exc
                    report['probes'][probe]=detail
                    if (detail.get('cleanup_errors') or detail.get('owned_window_close_error') or
                        detail.get('pointer_restore_error') or 'exceeded its runtime limit' in str(exc)):
                        raise
                    probe_errors.append(probe+': '+str(detail.get('error',exc)))
                    print(slug+': '+probe+' failed; recorded after helper cleanup',flush=True)
            command('check',[ROOT/'live.py','check','--receipt',receipt],20)
            if probe_errors:raise RuntimeError('; '.join(probe_errors))
            report['status']='passed'
        except Exception as exc:report['error']=str(exc)
        finally:
            if receipt:
                try:
                    restored=json.loads(command('restore',[ROOT/'live.py','restore','--receipt',receipt],60))
                    report['restore']=restored;print(slug+': '+restored['status'],flush=True)
                except Exception as exc:report.update(status='recovery-required',recovery_error=str(exc))
            (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');results.append(report)
            (batch/'report.json').write_text(json.dumps({'profiles':results},indent=2)+'\n')
            (ROOT/'verification/trial-latest.json').write_text(json.dumps({'run':str(batch),'profiles':results},indent=2)+'\n')
        if report['status']!='passed':
            summary={key:report[key] for key in ('profile','status','error','recovery_error','receipt') if key in report}
            summary['report']=str(out/'report.json')
            print(json.dumps(summary,indent=2));return 1
    print(json.dumps({'run':str(batch),'profiles':[r['profile'] for r in results],'status':'passed'}));return 0

if __name__=='__main__':raise SystemExit(main())
