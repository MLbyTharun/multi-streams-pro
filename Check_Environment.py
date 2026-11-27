"""Dependency and vision-API configuration diagnostic. Makes no API calls."""
import argparse
from importlib.util import find_spec
from importlib.metadata import version,PackageNotFoundError
import shutil,sys,platform

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--check-model',action='store_true',help='Also require API_BASE_URL and MODEL_NAME to be configured')
 a=p.parse_args()
 print('Python:',sys.version.split()[0], 'Platform:',platform.platform())
 errors=[]
 for mod,dist in [('PIL','Pillow'),('cv2','opencv-python'),('xlsxwriter','XlsxWriter'),('psutil','psutil'),('numpy','numpy'),('livekit.rtc','livekit'),('livekit.api','livekit-api')]:
  try:ok=find_spec(mod) is not None
  except ModuleNotFoundError:ok=False
  try:v=version(dist)
  except PackageNotFoundError:
   if mod=='cv2':
    try:v=version('opencv-python-headless')
    except PackageNotFoundError:v='missing'
   else:v='missing'
  print(f'{mod:19} {"OK" if ok else "MISSING":7} {v}')
  if not ok: errors.append(mod)
 for binary in ['ffmpeg','ffprobe','ffplay']:
  print(f'{binary:19}',shutil.which(binary) or 'NOT ON PATH')
  if binary in ['ffmpeg','ffprobe'] and not shutil.which(binary):errors.append(binary)
 import scene_config
 base=scene_config.resolved('API_BASE_URL');model=scene_config.resolved('MODEL_NAME')
 print('Vision API base URL:',base or 'NOT SET')
 print('Vision model name:  ',model or 'NOT SET')
 print('Vision API key:     ','set' if scene_config.resolved('API_KEY') else 'not set')
 if a.check_model and not (base and model):errors.append('vision API configuration')
 if errors:
  print('CHECK NEEDS ATTENTION:',', '.join(errors));return 1
 print('BASIC CHECK PASS (no API request or RTSP benchmark was run)');return 0
if __name__=='__main__':raise SystemExit(main())
