# Scene worker: LiveKit, RTSP and no-VR testing

Run all commands below from the backend repository root.

## Configuration: `scene_config.py`

Inference runs through a remote, OpenAI-compatible vision API
(`POST {API_BASE_URL}/chat/completions` with base64 JPEG images). No local model,
PyTorch, Hugging Face cache or GPU is needed.

Everything that is tuned often lives in one file,
[`scene_config.py`](scene_config.py):

| Setting | Purpose |
| --- | --- |
| `API_BASE_URL`, `API_KEY`, `MODEL_NAME` | **Left empty: fill them in yourself.** Any vision-capable chat-completions endpoint |
| `EXTRA_HEADERS`, `REQUEST_TIMEOUT_SECONDS`, `MAX_RETRIES`, `TEMPERATURE`, `MAX_OUTPUT_TOKENS` | Request tuning |
| `IMAGE_MAX_EDGE`, `JPEG_QUALITY` | Size/quality of images sent to the API |
| `SAMPLE_FPS`, `MAX_FRAMES_PER_CAMERA`, `MAX_CAMERAS`, `STALE_STREAM_SECONDS` | Live sampling of each camera angle |
| `TASK_DONE_CONFIRMATIONS` | Consecutive DONE windows needed before moving to the next task (default 2) |
| `MANAGER_MAX_PROCESSES` | Concurrent jobs in the HTTP manager (default 4) |
| `ACTION_PROMPT`, `TASK_PROGRESS_PROMPT` | The prompts |

If `API_BASE_URL`, `API_KEY` or `MODEL_NAME` is empty in the file, the worker
uses `SCENE_API_BASE_URL`, `SCENE_API_KEY` or `SCENE_MODEL_NAME` from the
environment (for example the ignored `scene-worker.env`). **Never commit a real
API key.** Starting inference without a base URL and model name fails with an
explicit error; transport tests work without them.

Docker Compose mounts `scene_config.py` read-only into the container, so edit
it on the host and restart the service (`docker compose ... restart scene-worker`);
no rebuild is needed. Check the configuration without calling the API:

```bash
python workers/scene-worker/Check_Environment.py --check-model
```

## One-command Docker test with your project's tasks

The local backend must already be running with your project data. No casting
session, camera, manually exported task file, or separate worker/LiveKit terminal
is needed for this test.

One-time setup: copy `scene-worker.env.example` to `scene-worker.env` if it does
not already exist. Set `SCENE_TEST_BACKEND_TOKEN` in that file to your current
login JWT (without `Bearer `), with permission to view this project's assets.
The backend requires authentication; this test does not bypass it.
The env file is ignored by Git and excluded from the Docker build.

The provided defaults are:

```dotenv
SCENE_TEST_BACKEND_URL=http://host.docker.internal:8080/api/authoringv2
SCENE_TEST_PROJECT_ID=6afd4d745b97303aa9215b7f
SCENE_TEST_ORGANIZATION_ID=6afd4d672b7827ebb469a41c
SCENE_TEST_TIMEOUT=600
```

Use the backend gateway on port `8080`, not the browser's port `8082`:
the frontend proxy on `8082` listens only on IPv6 localhost and is not reachable
from Docker. `host.docker.internal` reaches the Mac's gateway from Docker.
These are runtime env settings, not baked into the image; change them without
rebuilding. Never pass the JWT as a build argument.

Fill in the vision API settings first (see Configuration above).
The existing local mirror clock skew still requires `APT_MAX_FUTURE_TIME=86400`
in this Mac's ignored env file; the example keeps the normal 10-second default.
Fix the clock/mirror and restore the default when possible.

Then run just:

```bash
sh workers/scene-worker/test_project_video.sh \
  "/Users/hyperrealitytech/Downloads/IMG_3616.MOV"
```

The script builds the image, starts the Docker manager and isolated test
LiveKit, mounts only that video read-only, fetches every page of your project's
visible topics/tasks through the backend APIs, and sends the context to the
worker at startup. The recording loops as a camera through LiveKit, and the
first processed result is printed in the same terminal. Look for:

- `Loaded ... tasks across ... topics for project ...`
- `LIVEKIT TASK COMPLETED` with the completed and next task, or an audit such as
  `TASK IN PROGRESS`, `TASK DONE (CONFIRMING)` or `REVIEW`.

Missing IDs, missing/expired authentication and empty task lists fail explicitly;
the project test never silently falls back to task-free inference. This tests
API task retrieval plus the worker/LiveKit pipeline, not the casting lifecycle
or a real glasses app. The existing synthetic transport test remains separate.

The publisher stops its job and exits after the first processed result. Run
the same command again to retest. Docker services remain ready between tests;
stop only this test stack when finished (without deleting data):

```bash
docker compose -p trainxr-scene -f workers/scene-worker/docker-compose.yml \
  --profile project-test stop scene-worker livekit-test
```

## What was integrated

| Handoff mode | Available here | Web/backend integration |
| --- | --- | --- |
| `01_RTSP_Live` | RTSP runner and its shared core | Manager `mode: "rtsp"` |
| `02_Webcam_Live` | Shared webcam runner | Native webcam CLI; a webcam can also be published to LiveKit using the demo below |
| `03_Local_Saved_Videos` | Original core plus the unchanged batch runner | Native/Docker CLI, not a background HTTP upload API |
| New LiveKit adapter | `livekit_scene_worker.py` | Manager `mode: "livekit"` and authenticated backend casting endpoints |

All modes share `HyperReality_Final_Action_Review.infer_once`, which calls the
configured vision API (`vision_api.py`). The action prompt, parser and Excel
writers are otherwise unchanged; the file names still contain `Qwen2B` for
compatibility, but no Qwen model is loaded. The RTSP CLI adds an optional
`--output-dir` so HTTP jobs cannot accidentally read another job's results.

### Multiple camera angles and autonomous task progression

A LiveKit job subscribes to **every video track in the room** (or only those of
the participants listed in `participantIdentities`). Each track is one camera
angle of the same procedure. Every window, the newest frames of each fresh
angle (up to `MAX_FRAMES_PER_CAMERA` per camera, `MAX_CAMERAS` cameras) are sent
in **one** API request, labelled by camera and frame order.

With project tasks the worker is **fully autonomous**:

1. Tasks are flattened in the supplied order (topics, then their tasks).
2. Each window asks whether the **current** task is visibly complete; the model
   must answer `DONE` or `NOT_DONE` (`TASK_PROGRESS_PROMPT`).
3. After `TASK_DONE_CONFIRMATIONS` consecutive `DONE` windows the task is marked
   complete and the next task becomes current, immediately, without human input.
   A `NOT_DONE` resets the count. API errors or other answers are audited as
   `REVIEW` and do not change progress.
4. After the last task the worker publishes `ALL TASKS COMPLETE` and exits.

A camera that stops sending frames for `STALE_STREAM_SECONDS` is ignored while
the others continue; the job fails only if no camera sends frames for that long.
If inference is slower than the stride, an older pending window is replaced by
the newest one (`SKIPPED BACKLOG`), so judgements stay real time.

Without tasks, action recognition uses the original controlled action vocabulary
on the first fresh camera. Judgements are model output, **not human-verified**
(`verified: false`); a wrong `DONE` advances the procedure. Tune
`TASK_DONE_CONFIRMATIONS` and the prompt for your procedures.

## Communication

```text
Browser/webcam/VR -- video track --> LiveKit room --> Python scene worker
                                      ^
                                      |
                    reliable scene-worker.result data messages

Authenticated client --> backend casting API --> HTTP manager (start/status/stop)
```

LiveKit carries video **and result data** in LiveKit mode. HTTP manages the
worker's lifecycle and exposes its audit/results. RTSP mode is separate and
does not join LiveKit.

Data topic: `scene-worker.result`

```json
{
  "type": "scene-worker.result",
  "jobId": "...",
  "roomName": "...",
  "verified": false,
  "simulation": false,
  "kind": "audit",
  "data": {"status": "NO ACTION", "raw": "NONE", "model_processed": true}
}
```

An action proposal uses `kind: "action"` and the original action fields.
`simulation: true` identifies transport tests that deliberately do not call the API.
`autonomous: true` marks task-progression jobs.
Skipped/backlogged windows are reported explicitly. The frontend can listen to
LiveKit data packets on this topic; existing frontend code was not migrated.

With task context, the job first publishes an audit `TASK PROGRESS STARTED`
with the first task. Every judged window publishes an audit:

```json
{
  "type": "scene-worker.result", "jobId": "...", "roomName": "...",
  "verified": false, "autonomous": true, "simulation": false,
  "kind": "audit",
  "data": {
    "status": "TASK DONE (CONFIRMING)", "verdict": "DONE", "raw": "DONE",
    "camera_count": 2, "cameras": ["cam-left/TR_x", "cam-right/TR_y"],
    "sample_count": 12, "window_number": 7,
    "task": {"topic": "Brake repair", "title": "Loosen wheel nuts", "instruction": "..."},
    "progress": {"index": 0, "total": 2, "streak": 1, "finished": false,
                 "currentTitle": "Loosen wheel nuts", "completedTitles": []}
  }
}
```

Audit statuses: `TASK IN PROGRESS`, `TASK DONE (CONFIRMING)`, `TASK COMPLETED`,
`ALL TASKS COMPLETE`, `REVIEW` (API error or answer other than DONE/NOT_DONE)
and `SKIPPED BACKLOG`. When a task is completed, an action follows:

```json
{
  "type": "scene-worker.result", "jobId": "...", "roomName": "...",
  "verified": false, "autonomous": true, "simulation": false,
  "kind": "action",
  "data": {
    "action": "Loosen wheel nuts", "event": "task_completed",
    "completedTask": {"topic": "Brake repair", "title": "Loosen wheel nuts", "instruction": "..."},
    "nextTask": {"topic": "Brake repair", "title": "Remove wheel", "instruction": "..."},
    "index": 1, "total": 2, "status": "AUTONOMOUS"
  }
}
```

For the last task `event` is `all_complete` and `nextTask` is `null`. The client
subscribes to the topic and shows the current task; publishing does not control
glasses.

## Installed environment

- Docker image: `scene-worker:local`, Python 3.12, FFmpeg, OpenCV (headless),
  Pillow and LiveKit SDK. No PyTorch, Transformers or model cache.
- Native environment: `.venv/` created by `setup_mac.sh` from `requirements-mac.txt`.
- Inference needs network access from the worker to `API_BASE_URL`.

To recreate the native environment:

```bash
sh workers/scene-worker/setup_mac.sh
PY="$PWD/workers/scene-worker/.venv/bin/python"
"$PY" workers/scene-worker/Check_Environment.py --check-model
```

Run the unit tests (no network or model) from `workers/scene-worker`:

```bash
python -m unittest test_vision_api test_task_progress test_livekit_progress test_scene_worker_manager
```

## Easiest no-VR test: Docker, no camera or model inference

The disposable test LiveKit server is isolated from your existing LiveKit
service and has no published host ports.

```bash
docker compose -p trainxr-scene -f workers/scene-worker/docker-compose.yml \
  --profile test run --rm test-publisher
```

Expected output: `LIVEKIT TRANSPORT PASS`, `frames_received >= 2`,
`sample_count: 2`, `simulation: true`, `model_processed: false`.

This publishes moving synthetic video through a real LiveKit room. It verifies
video reception, worker start/stop, and reliable result packets, without VR.
It does **not** claim the model recognized an object. Add `--cameras 3` to the
publisher command to publish three angles; the result then reports `camera_count: 3`.

Stop only this test stack afterward (no volumes are removed):

```bash
docker compose -p trainxr-scene -f workers/scene-worker/docker-compose.yml \
  --profile test stop scene-worker livekit-test
```

## Native no-VR test, including real API inference

Start a separate local development LiveKit server in terminal 1:

```bash
docker run --rm --name trainxr-scene-demo-livekit \
  -p 127.0.0.1:17980:7880 -p 127.0.0.1:17981:7881 \
  -p 127.0.0.1:17982:17982/udp \
  livekit/livekit-server:v1.9.9 \
  --dev --bind 0.0.0.0 --node-ip 127.0.0.1 --udp-port 17982
```

In terminal 2, start the native manager:

```bash
SCENE_WORKER_HOST=127.0.0.1 SCENE_WORKER_PORT=18095 \
  workers/scene-worker/.venv/bin/python workers/scene-worker/scene_worker_manager.py
```

In terminal 3:

```bash
export LIVEKIT_API_KEY=devkey
export LIVEKIT_API_SECRET=secret
PY="$PWD/workers/scene-worker/.venv/bin/python"

# Fast transport test: no API call/camera.
"$PY" workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095

# Same with three camera angles of the scene.
"$PY" workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095 --cameras 3

# Real inference on synthetic video; NONE is a valid result.
"$PY" workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095 \
  --inference --timeout 180

# Substitute a saved recording; replayed as a LiveKit camera.
"$PY" workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095 \
  --video "/absolute/path/to/your-recording.mp4" --inference --timeout 180

# Or explicitly opt into your Mac webcam (grant macOS camera permission).
"$PY" workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095 \
  --camera 0 --inference --timeout 180
```

The short `devkey`/`secret` keys are only for the isolated development server.
Never use them in production. The demo creates its own room/tokens; a real
browser/VR session must use tokens minted by the backend.
The saved-video LiveKit demo reads at 10 FPS, loops at EOF, and resizes to
320x180; it is a pipeline test, not a source-timing benchmark.
Use Ctrl+C in terminals 1 and 2 to stop the demo services.

## Analyze saved recordings directly (mode 03)

No LiveKit or VR is needed:

```bash
cd workers/scene-worker

.venv/bin/python HyperReality_Final_Action_Review.py analyze \
  --video "/absolute/path/to/your-recording.mp4"

.venv/bin/python HyperReality_Qwen2B_Run_Good_Case_Actions.py \
  --videos-dir "/absolute/path/to/your-video-folder"
```

Reports/audits go under `outputs/`. No videos are included in the handoff.
Use recordings you are authorized to process. Never run the original `approve`
command without human review.

## HTTP manager and Docker startup

```bash
docker compose -p trainxr-scene -f workers/scene-worker/docker-compose.yml up
curl --fail http://127.0.0.1:8090/healthz
```

To rebuild:

```bash
docker compose -p trainxr-scene -f workers/scene-worker/docker-compose.yml build scene-worker
```

The local Debian mirror advertised metadata about a day ahead of the build clock.
If this produces `Release file ... is not valid yet`, fix the clock/mirror first.
For this local machine the build was validated with
`build --build-arg APT_MAX_FUTURE_TIME=86400 scene-worker`: a bounded future-date
tolerance, not disabled signature or expiry verification. The default remains 10 seconds.

The manager port is published only on localhost. To require authentication,
copy `scene-worker.env.example` to the ignored `scene-worker.env` and replace
the example token with a real value; send `Authorization: Bearer ...`.
Do not expose the manager directly to the Internet or put worker JWTs/signing
keys in a browser. Browsers use their own backend-issued participant tokens.

Manager endpoints:

- `POST /jobs`: `mode: "livekit"`, `"rtsp"`, `"test-connection"`, or `"self-test"`.
- `GET /jobs/{id}`: status, `latestAction`, `recentEvents`, diagnostic logs.
- `DELETE /jobs/{id}`: stop the job.
- `GET /jobs?key=...`: recover jobs started with an `idempotencyKey`.
- `DELETE /jobs?key=...`: cancel a start even if its delayed POST has not arrived.
- `GET /healthz`: dependency/device diagnostics, not a model-accuracy test.

Room tokens are passed to the LiveKit subprocess through its environment,
not command arguments or public job metadata. Output folders are isolated per
job. Start/status history is in memory; output audits and cancellation tombstones
remain on disk. Cancelled attempt keys cannot be reused; an intentional new start
must use a new attempt key.

RTSP example:

```bash
curl --fail -X POST http://127.0.0.1:8090/jobs \
  -H 'Content-Type: application/json' \
  -d '{"mode":"rtsp","rtspUrl":"rtsp://host.docker.internal:8554/hyperreality","window":3,"stride":3,"seconds":60}'
```

LiveKit jobs also accept `"participantIdentities": ["cam-left", "cam-right"]`
to restrict which participants' cameras are used; by default every video track
in the room is a camera angle. RTSP jobs analyse one stream each; run several
jobs (up to `MANAGER_MAX_PROCESSES`) for several RTSP sources.

Use `host.docker.internal` only when the RTSP source is on the Mac; for the
optional replay service on this compose network, use `rtsp://mediamtx:8554/...`.
Webcam capture should run natively; Docker Desktop does not provide ordinary
Mac webcam device passthrough.

## Backend integration (opt-in)

These routes belong to the current `authoringv2` casting API, not the legacy
`authoring` service. They require an authenticated JWT with the repository's
`ADMIN` permission, relative to your configured API base/global prefix:

```text
POST   /casting/sessions/:sessionId/scene-worker
GET    /casting/sessions/:sessionId/scene-worker
DELETE /casting/sessions/:sessionId/scene-worker
```

Use an existing live casting session. POST accepts `{}` for the original action
recognition or `{"transportTest":true}` to verify video/result transport without
calling the API. To enable autonomous task progression, POST both MongoDB IDs:

```json
{
  "projectId": "<project-id>",
  "organizationId": "<organization-id>"
}
```

For example, using your configured API base URL, admin JWT and existing session:

```bash
curl --fail-with-body -X POST "$API_BASE/casting/sessions/$SESSION_ID/scene-worker" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "x-organization-id: $ORGANIZATION_ID" \
  -H 'Content-Type: application/json' \
  -d "{\"projectId\":\"$PROJECT_ID\",\"organizationId\":\"$ORGANIZATION_ID\"}"
```

The backend checks project/organization scope, fetches every page of the project's
normally visible topics and their tasks (superseded topics are excluded as in
the topics API), then passes topic names, task titles and text instructions to
the worker. Tasks not associated with a topic are not included. Empty task lists
and task context over 3 MiB are rejected. Only the current and next task are
sent to the API per request, so long lists do not grow the prompt. The list is a startup snapshot:
stop and start a new worker to pick up edits. If a start is being retried, changing
the list can conflict with the original attempt; stop it before starting again.

Start is explicit: creating a session does not automatically launch inference.
The regular session-stop lifecycle also cleans up its associated worker.

The context is saved in `outputs/<job-id>/task-context.json` with owner-only
file permissions and persists with the job audit after stopping. Protect and
manage these files as project data. The manager must remain trusted and private.

To try autonomous progression with a recording without a backend session, create a JSON
file yourself containing representative, authorized tasks:

```json
{"topics":[{"title":"Brake repair","tasks":[
  {"title":"Loosen wheel nuts","instruction":"Loosen the wheel nuts."},
  {"title":"Remove wheel","instruction":"Remove the wheel."}
]}]}
```

Restart the native manager after code changes, then use the earlier LiveKit
development setup and run:

```bash
workers/scene-worker/.venv/bin/python workers/scene-worker/test_livekit_transport.py \
  --url ws://127.0.0.1:17980 --manager http://127.0.0.1:18095 \
  --video "/absolute/path/to/your-recording.mov" \
  --task-context "/absolute/path/to/your-tasks.json" --inference --timeout 180
```

The terminal shows either `LIVEKIT TASK COMPLETED` with the completed and next
task, or the first judged audit (`TASK IN PROGRESS`, `TASK DONE (CONFIRMING)` or
`REVIEW`). This demo sends its own JSON
directly to the manager; it does not test the backend's database lookup. To test
that part, start an actual casting session with the backend request above and
subscribe to the same LiveKit room, or inspect the backend worker status route.
Task progression is unit-tested with a mocked API; real accuracy depends on the
model you configure and has not been validated for your procedures or glasses.

Set these in the backend's environment, then restart only that backend service:

```dotenv
SCENE_WORKER_ENABLED=true
SCENE_WORKER_MANAGER_URL=http://127.0.0.1:8090
SCENE_WORKER_LIVEKIT_URL=ws://host.docker.internal:7880
SCENE_WORKER_TIMEOUT_MS=10000
```

`SCENE_WORKER_LIVEKIT_URL` is the URL reachable **from the Python worker**:
for a native worker use `ws://127.0.0.1:7880`; for a Docker worker joining the
existing host-published LiveKit use `ws://host.docker.internal:7880`; on a shared
Docker network use the actual LiveKit service name. Omit the override to use the
existing backend LiveKit URL. The browser URL is not changed.

Set the same nonempty `SCENE_WORKER_API_TOKEN` in both backend and manager;
the backend requires it. Existing LiveKit signing credentials remain in the backend;
it mints a separate room-scoped subscriber/data-publisher JWT for the worker.
WebRTC ICE/TCP/UDP connectivity must be reachable from the worker as well as the
WebSocket URL. The isolated Docker transport test does not validate an existing
production LiveKit deployment's networking.

Every start reserves a unique attempt key in the casting session. Retries recover
the same job, and stop durably cancels that attempt so a delayed start cannot
recreate it. An intentional later start gets a new key. Leave
`SCENE_WORKER_ENABLED=false` (the default) to preserve the existing no-worker flow.

Backend route/service tests use mocked manager calls. The backend does not send
`participantIdentity`, so worker jobs use every camera angle in the casting room.
The repository-wide TypeScript check has unrelated pre-existing errors.
