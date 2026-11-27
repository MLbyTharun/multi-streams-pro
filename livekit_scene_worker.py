"""Receive every camera angle in a LiveKit room and publish results to the same room.

With task context the worker is autonomous: each window sends all fresh camera
angles to the vision API together with the current task, and moves to the next
task after scene_config.TASK_DONE_CONFIRMATIONS consecutive DONE verdicts.
Without task context it runs the original action recognition on one camera.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from livekit import rtc
from PIL import Image

import HyperReality_Final_Action_Review as core
import HyperReality_Qwen2B_Live_RTSP_Developer as rtsp
import HyperReality_Qwen2B_Live_Webcam_Developer as dev
import scene_config
import vision_api
from task_progress import TaskProgress, flatten_tasks, parse_verdict

TOPIC = "scene-worker.result"
STATUS = {
    "in_progress": "TASK IN PROGRESS",
    "confirming": "TASK DONE (CONFIRMING)",
    "task_completed": "TASK COMPLETED",
    "all_complete": "ALL TASKS COMPLETE",
}


def evenly(items: list, limit: int) -> list:
    """Up to `limit` items spread across the list, always keeping the newest."""
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[-1]]
    step = (len(items) - 1) / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


class CameraBuffers:
    """Recent samples per camera angle (stream id = participant/track sid)."""

    def __init__(self, samples_per_window: int, max_cameras: int = scene_config.MAX_CAMERAS,
                 stale_seconds: float = scene_config.STALE_STREAM_SECONDS):
        self.samples_per_window = samples_per_window
        self.max_cameras = max_cameras
        self.stale_seconds = stale_seconds
        self.samples: dict[str, deque] = {}
        self.last_seen: dict[str, float] = {}

    def add(self, stream_id: str, now: float, image, path: str, offset: float) -> None:
        buffer = self.samples.setdefault(stream_id, deque(maxlen=self.samples_per_window))
        buffer.append((image, path, offset))
        self.last_seen[stream_id] = now

    def remove(self, stream_id: str) -> None:
        self.samples.pop(stream_id, None)
        self.last_seen.pop(stream_id, None)

    def fresh(self, now: float) -> list[str]:
        streams = [s for s in self.samples if now - self.last_seen[s] <= self.stale_seconds]
        return streams[:self.max_cameras]

    def ready(self, now: float) -> bool:
        return any(len(self.samples[s]) >= self.samples_per_window for s in self.fresh(now))

    def window(self, now: float, max_frames: int = scene_config.MAX_FRAMES_PER_CAMERA) -> dict[str, list]:
        return {s: evenly(list(self.samples[s]), max_frames)
                for s in self.fresh(now) if self.samples[s]}


@dataclass
class MultiWindow:
    number: int
    cameras: dict[str, list]  # stream id -> [(image, path, offset)]
    enqueued_at: float = field(default_factory=time.perf_counter)

    @property
    def sample_count(self) -> int:
        return sum(len(samples) for samples in self.cameras.values())


def put_latest(jobs: queue.Queue, job: MultiWindow, journal) -> None:
    """Never let inference lag behind real time: a newer window replaces a pending one."""
    try:
        jobs.put_nowait(job)
        return
    except queue.Full:
        pass
    try:
        old = jobs.get_nowait()
    except queue.Empty:
        old = None
    else:
        jobs.task_done()
    if old is not None:
        journal.add({
            "status": "SKIPPED BACKLOG", "model_processed": False, "inference_attempted": False,
            "window_number": old.number, "camera_count": len(old.cameras),
            "sample_count": old.sample_count,
            "reason": "Replaced an older pending window with newer camera observations",
        })
    jobs.put_nowait(job)


def task_payload(task: dict | None) -> dict | None:
    if task is None:
        return None
    return {"topic": task["topic"], "title": task["title"], "instruction": task["instruction"]}


def judge_window(progress: TaskProgress, window: dict[str, list], journal, number: int) -> None:
    """Ask the API whether the current task is done, using every camera angle at once."""
    images = []
    for camera, (stream_id, samples) in enumerate(window.items()):
        for index, (image, _path, _offset) in enumerate(samples):
            images.append((f"Camera {camera + 1} ({stream_id}), frame {index}.", image))
    task = progress.current()
    audit = {
        "model_processed": False, "inference_attempted": True, "window_number": number,
        "camera_count": len(window), "cameras": list(window), "sample_count": len(images),
        "task": task_payload(task),
    }
    try:
        raw, tokens = vision_api.complete(progress.prompt(len(window)), images)
        audit.update(raw=raw, output_tokens=tokens, model_processed=True)
        verdict = parse_verdict(raw)
    except (RuntimeError, ValueError, OSError) as exc:
        audit.update(status="REVIEW", reason=str(exc), progress=progress.snapshot())
        journal.add(audit)
        return
    event = progress.observe(verdict)
    audit.update(status=STATUS[event["event"]], verdict=verdict, progress=progress.snapshot())
    action = None
    if event["completed"] is not None:
        action = {
            "action": event["completed"]["title"], "event": event["event"],
            "completedTask": task_payload(event["completed"]),
            "nextTask": task_payload(event["current"]),
            "index": event["index"], "total": event["total"], "status": "AUTONOMOUS",
        }
    journal.add(audit, action)


async def run(args: argparse.Namespace) -> None:
    token = os.environ.get("SCENE_WORKER_LIVEKIT_TOKEN", "")
    if not token:
        raise ValueError("SCENE_WORKER_LIVEKIT_TOKEN is required")
    folder = Path(args.output_dir)
    images = folder / "sampled_images"
    images.mkdir(parents=True, exist_ok=True)
    fps = scene_config.SAMPLE_FPS
    per_window = args.window * fps
    dev.WINDOW_SECONDS = float(args.window)
    dev.SAMPLES_PER_WINDOW = per_window
    journal = dev.LiveJournal(folder, core, datetime.now().astimezone().isoformat())
    identities = {identity for identity in args.participant_identity if identity}
    progress = None
    if args.task_context:
        with Path(args.task_context).open(encoding="utf-8") as handle:
            tasks = flatten_tasks(json.load(handle))
        if not tasks:
            raise ValueError("task context contains no tasks")
        progress = TaskProgress(tasks, scene_config.TASK_DONE_CONFIRMATIONS)
        print(f"Loaded {len(tasks)} tasks; autonomous progression starts at '{tasks[0]['title']}'", flush=True)
    if not args.transport_test:
        vision_api.ensure_configured()
    baseline = core.PROMPT

    def action_infer(frames):
        core.PROMPT = rtsp.fitted_prompt(core, len(frames), baseline)
        return core.infer_once(frames)

    room = rtc.Room()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except NotImplementedError:
            pass
    receivers: dict[str, asyncio.Task] = {}
    latest: dict[str, tuple[float, Image.Image]] = {}
    frames_received = 0
    buffers = CameraBuffers(per_window)
    jobs: queue.Queue = queue.Queue(maxsize=1)

    async def receive(stream_id: str, track: rtc.RemoteVideoTrack) -> None:
        nonlocal frames_received
        stream = rtc.VideoStream(track, capacity=1, format=rtc.VideoBufferType.RGB24)
        try:
            async for event in stream:
                frame = event.frame
                image = Image.frombytes("RGB", (frame.width, frame.height), bytes(frame.data))
                image.thumbnail((core.MODEL_EDGE, core.MODEL_EDGE))
                latest[stream_id] = time.perf_counter(), image
                frames_received += 1
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def subscribed(track, publication, participant):
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        if identities and participant.identity not in identities:
            return
        stream_id = f"{participant.identity}/{track.sid}"
        if stream_id not in receivers or receivers[stream_id].done():
            receivers[stream_id] = asyncio.create_task(receive(stream_id, track))
            print(f"Receiving camera {stream_id}", flush=True)

    @room.on("track_unsubscribed")
    def unsubscribed(track, publication, participant):
        stream_id = f"{participant.identity}/{track.sid}"
        task = receivers.pop(stream_id, None)
        if task is not None:
            task.cancel()
        latest.pop(stream_id, None)
        buffers.remove(stream_id)
        print(f"Camera left: {stream_id}", flush=True)

    @room.on("disconnected")
    def disconnected(reason):
        stopped.set()

    async def sample() -> None:
        last_stamp: dict[str, float] = {}
        slot = 0
        started = time.perf_counter()
        journal.start_perf = started
        next_window = None
        number = 0
        last_frame = started
        while not stopped.is_set():
            now = time.perf_counter()
            if args.seconds and now - started >= args.seconds:
                stopped.set()
                return
            for stream_id, (stamp, image) in list(latest.items()):
                if last_stamp.get(stream_id) == stamp:
                    continue
                last_stamp[stream_id] = stamp
                last_frame = now
                safe = stream_id.replace("/", "_").replace("\\", "_")
                path = images / f"{safe}_f{slot + 1:06d}.jpg"
                image.save(path)
                buffers.add(stream_id, now, image, str(path.resolve()), now - started)
            slot += 1
            if next_window is None and buffers.ready(now):
                next_window = now
            if next_window is not None and now >= next_window:
                window = buffers.window(now)
                if window:
                    put_latest(jobs, MultiWindow(number, window), journal)
                    number += 1
                next_window = now + args.stride
            if now - last_frame > scene_config.STALE_STREAM_SECONDS:
                raise RuntimeError(
                    f"No matching LiveKit video frames for {scene_config.STALE_STREAM_SECONDS} seconds")
            await asyncio.sleep(1 / fps)

    def process(job: MultiWindow) -> None:
        if args.transport_test:
            journal.add({
                "status": "TRANSPORT TEST", "model_processed": False,
                "inference_attempted": False, "frames_received": frames_received,
                "camera_count": len(job.cameras), "cameras": list(job.cameras),
                "sample_count": job.sample_count, "window_number": job.number,
                "reason": "Real LiveKit frames; the vision API was deliberately not called",
            })
        elif progress is not None:
            judge_window(progress, job.cameras, journal, job.number)
        else:
            samples = next(iter(job.cameras.values()))
            offsets = [s[2] for s in samples]
            single = dev.WindowJob(
                job.number, offsets[0], offsets[-1] + 1 / fps, [s[0] for s in samples],
                [s[1] for s in samples], job.enqueued_at, offsets,
            )
            dev.run_one(core, single, action_infer, journal)

    async def infer() -> None:
        while not stopped.is_set() or not jobs.empty():
            if progress is not None and progress.finished:
                stopped.set()
                return
            try:
                job = jobs.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            try:
                await asyncio.to_thread(process, job)
            finally:
                jobs.task_done()

    offset = 0

    async def drain() -> None:
        nonlocal offset
        if not journal.file.is_file():
            return
        with journal.file.open(encoding="utf-8") as handle:
            handle.seek(offset)
            while line := handle.readline():
                if not line.endswith("\n"):
                    break
                event = json.loads(line)
                payload = {
                    "type": TOPIC, "jobId": args.job_id,
                    "roomName": room.name, "verified": False,
                    "autonomous": progress is not None and not args.transport_test,
                    "simulation": args.transport_test, **event,
                }
                # Frame paths and raw long responses stay in the HTTP audit.
                data = dict(payload["data"])
                for key in ("sample_paths", "before", "during", "after"):
                    data.pop(key, None)
                if isinstance(data.get("raw"), str):
                    data["raw"] = data["raw"][:2000]
                payload["data"] = data
                await room.local_participant.publish_data(
                    json.dumps(payload), reliable=True, topic=TOPIC,
                )
                offset = handle.tell()

    async def publish() -> None:
        while True:
            await drain()
            await asyncio.sleep(0.1)

    tasks: list[asyncio.Task] = []
    try:
        await room.connect(args.url, token)
        print(f"Joined LiveKit room {room.name}; transportTest={args.transport_test}", flush=True)
        if progress is not None:
            journal.add({
                "status": "TASK PROGRESS STARTED", "model_processed": False,
                "inference_attempted": False, "task": task_payload(progress.current()),
                "progress": progress.snapshot(),
            })
        tasks = [asyncio.create_task(coro()) for coro in (sample, infer, publish)]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        stopped.set()
        await tasks[1]
        tasks[2].cancel()
        await asyncio.gather(tasks[2], return_exceptions=True)
        await drain()
        if progress is not None and progress.finished:
            print("ALL TASKS COMPLETE; stopping worker", flush=True)
    finally:
        stopped.set()
        tasks.extend(receivers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await room.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--participant-identity", action="append", default=[],
                        help="Only use cameras from this participant; repeat for several. Default: all")
    parser.add_argument("--window", type=int, choices=[1, 2, 3, 5], default=3)
    parser.add_argument("--stride", type=int, choices=[1, 2, 3, 5], default=3)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--transport-test", action="store_true")
    parser.add_argument("--task-context")
    args = parser.parse_args()
    if args.stride > args.window or args.seconds < 0:
        parser.error("stride must not exceed window; seconds must be nonnegative")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
