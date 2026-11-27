import unittest

from task_progress import TaskProgress, flatten_tasks, parse_verdict

CONTEXT = {"topics": [
    {"title": "Wheel", "tasks": [
        {"title": "Loosen nuts", "instruction": "Loosen the wheel nuts."},
        {"title": "Remove wheel", "instruction": "Remove the wheel."},
    ]},
    {"title": "Brake", "tasks": [{"title": "Inspect pads", "instruction": "Inspect the pads."}]},
]}


class FlattenTests(unittest.TestCase):
    def test_preserves_topic_then_task_order(self):
        tasks = flatten_tasks(CONTEXT)
        self.assertEqual([t["title"] for t in tasks], ["Loosen nuts", "Remove wheel", "Inspect pads"])
        self.assertEqual(tasks[2]["topic"], "Brake")


class VerdictTests(unittest.TestCase):
    def test_accepts_done_and_not_done_variants(self):
        for raw, expected in [("DONE", "DONE"), ("done.", "DONE"), ("Done\nThe wheel is off.", "DONE"),
                              ("NOT_DONE", "NOT_DONE"), ("not_done\nnuts still on", "NOT_DONE"),
                              ("NOT DONE", "NOT_DONE"), ("**DONE**", "DONE")]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_verdict(raw), expected)

    def test_rejects_other_answers(self):
        for raw in ("yes", "", "maybe DONE", "DONE-ish"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_verdict(raw)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.progress = TaskProgress(flatten_tasks(CONTEXT), confirmations=2)

    def test_advances_only_after_consecutive_confirmations(self):
        first = self.progress.observe("DONE")
        self.assertEqual(first["event"], "confirming")
        self.assertEqual(self.progress.index, 0)
        second = self.progress.observe("DONE")
        self.assertEqual(second["event"], "task_completed")
        self.assertEqual(second["completed"]["title"], "Loosen nuts")
        self.assertEqual(second["current"]["title"], "Remove wheel")
        self.assertEqual(self.progress.index, 1)
        self.assertEqual(self.progress.streak, 0)

    def test_not_done_resets_streak(self):
        self.progress.observe("DONE")
        event = self.progress.observe("NOT_DONE")
        self.assertEqual(event["event"], "in_progress")
        self.assertEqual(self.progress.streak, 0)
        self.assertEqual(self.progress.observe("DONE")["event"], "confirming")
        self.assertEqual(self.progress.index, 0)

    def test_last_task_reports_all_complete_once(self):
        for _ in range(4):
            self.progress.observe("DONE")
        self.progress.observe("DONE")
        event = self.progress.observe("DONE")
        self.assertEqual(event["event"], "all_complete")
        self.assertEqual(event["completed"]["title"], "Inspect pads")
        self.assertTrue(self.progress.finished)
        self.assertIsNone(self.progress.current())
        with self.assertRaises(RuntimeError):
            self.progress.observe("DONE")

    def test_single_confirmation_advances_immediately(self):
        progress = TaskProgress(flatten_tasks(CONTEXT), confirmations=1)
        self.assertEqual(progress.observe("DONE")["event"], "task_completed")

    def test_prompt_contains_current_and_next_task(self):
        prompt = self.progress.prompt(camera_count=2)
        self.assertIn("2 camera angle(s)", prompt)
        self.assertIn("Current task 1 of 3", prompt)
        self.assertIn("Loosen the wheel nuts.", prompt)
        self.assertIn("Remove wheel", prompt)

    def test_prompt_for_last_task_has_no_next_task(self):
        progress = TaskProgress(flatten_tasks(CONTEXT)[2:], confirmations=1)
        self.assertIn("Next task: none (this is the last task)", progress.prompt(camera_count=1))

    def test_prompt_survives_braces_in_task_text(self):
        progress = TaskProgress([{"topic": "T", "title": "Set {torque}", "instruction": "Use {x}"}], 1)
        self.assertIn("Set {torque}", progress.prompt(camera_count=1))

    def test_empty_task_list_rejected(self):
        with self.assertRaises(ValueError):
            TaskProgress([], confirmations=2)

    def test_snapshot(self):
        self.progress.observe("DONE")
        self.assertEqual(self.progress.snapshot(), {
            "index": 0, "total": 3, "streak": 1, "finished": False,
            "currentTitle": "Loosen nuts", "completedTitles": [],
        })


if __name__ == "__main__":
    unittest.main()
