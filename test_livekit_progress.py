import queue
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import livekit_scene_worker as worker
from task_progress import TaskProgress, flatten_tasks

CONTEXT = {"topics": [{"title": "Wheel", "tasks": [
    {"title": "Loosen nuts", "instruction": "Loosen the wheel nuts."},
    {"title": "Remove wheel", "instruction": "Remove the wheel."},
]}]}


def frames(count):
    return [(Image.new("RGB", (8, 8)), f"p{i}.jpg", i / 2) for i in range(count)]


class JudgeWindowTests(unittest.TestCase):
    def setUp(self):
        self.progress = TaskProgress(flatten_tasks(CONTEXT), confirmations=2)
        self.journal = Mock()
        self.window = {"cam-a/TR_1": frames(4), "cam-b/TR_2": frames(4)}

    def test_all_camera_angles_go_into_one_request(self):
        with patch.object(worker.vision_api, "complete", return_value=("NOT_DONE", 2)) as complete:
            worker.judge_window(self.progress, self.window, self.journal, 0)
        prompt, images = complete.call_args.args
        self.assertEqual(len(images), 8)
        self.assertIn("2 camera angle(s)", prompt)
        self.assertIn("Loosen the wheel nuts.", prompt)
        self.assertEqual(images[0][0], "Camera 1 (cam-a/TR_1), frame 0.")
        self.assertEqual(images[4][0], "Camera 2 (cam-b/TR_2), frame 0.")
        audit = self.journal.add.call_args.args[0]
        self.assertEqual((audit["status"], audit["camera_count"]), ("TASK IN PROGRESS", 2))

    def test_consecutive_done_windows_complete_task_and_advance(self):
        with patch.object(worker.vision_api, "complete", return_value=("DONE\nnuts are loose", 2)):
            worker.judge_window(self.progress, self.window, self.journal, 0)
            self.assertEqual(self.journal.add.call_args.args[0]["status"], "TASK DONE (CONFIRMING)")
            self.assertEqual(len(self.journal.add.call_args.args), 2)
            self.assertIsNone(self.journal.add.call_args.args[1])
            worker.judge_window(self.progress, self.window, self.journal, 1)
        audit, action = self.journal.add.call_args.args
        self.assertEqual(audit["status"], "TASK COMPLETED")
        self.assertEqual(action["completedTask"]["title"], "Loosen nuts")
        self.assertEqual(action["nextTask"]["title"], "Remove wheel")
        self.assertEqual(action["status"], "AUTONOMOUS")
        self.assertEqual(self.progress.index, 1)

    def test_api_error_is_reviewed_without_changing_progress(self):
        self.progress.observe("DONE")
        with patch.object(worker.vision_api, "complete", side_effect=RuntimeError("HTTP 503")):
            worker.judge_window(self.progress, self.window, self.journal, 0)
        audit = self.journal.add.call_args.args[0]
        self.assertEqual(audit["status"], "REVIEW")
        self.assertIn("503", audit["reason"])
        self.assertEqual((self.progress.index, self.progress.streak), (0, 1))

    def test_unparseable_answer_is_reviewed(self):
        with patch.object(worker.vision_api, "complete", return_value=("I think so", 3)):
            worker.judge_window(self.progress, self.window, self.journal, 0)
        audit = self.journal.add.call_args.args[0]
        self.assertEqual(audit["status"], "REVIEW")
        self.assertEqual(audit["raw"], "I think so")

    def test_last_task_reports_all_complete(self):
        progress = TaskProgress(flatten_tasks(CONTEXT)[1:], confirmations=1)
        with patch.object(worker.vision_api, "complete", return_value=("DONE", 1)):
            worker.judge_window(progress, self.window, self.journal, 0)
        audit, action = self.journal.add.call_args.args
        self.assertEqual(audit["status"], "ALL TASKS COMPLETE")
        self.assertIsNone(action["nextTask"])
        self.assertTrue(progress.finished)


class CameraBufferTests(unittest.TestCase):
    def test_stale_camera_dropped_while_others_continue(self):
        buffers = worker.CameraBuffers(samples_per_window=2, max_cameras=4, stale_seconds=15)
        for t in (0.0, 0.5):
            buffers.add("a", t, "img", "p", t)
        for t in (0.0, 0.5, 20.0, 20.5):
            buffers.add("b", t, "img", "p", t)
        self.assertEqual(list(buffers.window(21.0)), ["b"])
        self.assertEqual(list(buffers.window(10.0)), ["a", "b"])

    def test_ready_needs_a_full_window_from_a_fresh_camera(self):
        buffers = worker.CameraBuffers(samples_per_window=3)
        buffers.add("a", 0.0, "img", "p", 0)
        buffers.add("a", 0.5, "img", "p", 0.5)
        self.assertFalse(buffers.ready(1.0))
        buffers.add("a", 1.0, "img", "p", 1.0)
        self.assertTrue(buffers.ready(1.0))

    def test_camera_and_frame_limits(self):
        buffers = worker.CameraBuffers(samples_per_window=10, max_cameras=2)
        for name in ("a", "b", "c"):
            for t in range(10):
                buffers.add(name, 1.0, t, "p", t)
        window = buffers.window(1.0, max_frames=4)
        self.assertEqual(list(window), ["a", "b"])
        self.assertEqual([sample[0] for sample in window["a"]], [0, 3, 6, 9])

    def test_removed_camera_is_forgotten(self):
        buffers = worker.CameraBuffers(samples_per_window=1)
        buffers.add("a", 0.0, "img", "p", 0)
        buffers.remove("a")
        self.assertEqual(buffers.window(0.0), {})


class BacklogTests(unittest.TestCase):
    def test_newer_window_replaces_pending_one(self):
        jobs, journal = queue.Queue(maxsize=1), Mock()
        worker.put_latest(jobs, worker.MultiWindow(0, {"a": frames(2)}), journal)
        worker.put_latest(jobs, worker.MultiWindow(1, {"a": frames(2)}), journal)
        self.assertEqual(jobs.get_nowait().number, 1)
        skipped = journal.add.call_args.args[0]
        self.assertEqual((skipped["status"], skipped["window_number"]), ("SKIPPED BACKLOG", 0))


if __name__ == "__main__":
    unittest.main()
