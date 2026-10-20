"""Read-only UI test host; never starts dispatchers and rejects every POST."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.main import ModularHubHandler
from server.routes.api_router import send_json
from http.server import ThreadingHTTPServer
import server.routes.api_router as router
from server.config import OUTPUT_DIR, MODELS_DIR, APPARELS_DIR, SCENES_DIR, TEMPLATES_DIR
from server.services.asset_service import get_dir_images
video=next(Path(OUTPUT_DIR).rglob('*.mp4'))
fixtures=[{'task_id':f'{i:03d}','template':next(Path(TEMPLATES_DIR).glob('*.json')).name,
 'model':get_dir_images(MODELS_DIR)[0],'apparel':get_dir_images(APPARELS_DIR)[i%len(get_dir_images(APPARELS_DIR))],
 'scene':get_dir_images(SCENES_DIR)[0],'status':'DONE' if i%5==0 else ['PENDING','RUNNING','FAILED','QUEUED'][i%4],
 'output_file':video.relative_to(OUTPUT_DIR).as_posix() if i%5==0 else '', 'worker_node':'主端口' if i%5==0 else ''} for i in range(1,51)]
router.load_tasks=lambda: fixtures
router.load_tasks_unlocked=lambda: fixtures
class ReadOnlyHandler(ModularHubHandler):
    def do_POST(self):
        send_json(self,{'success':False,'message':'Read-only UI test server'},403)
if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',19099),ReadOnlyHandler).serve_forever()
