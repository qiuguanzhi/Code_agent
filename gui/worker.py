"""Background worker placeholder for the Phase 4 desktop skeleton."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class AgentWorker(QThread):
    """Emit deterministic mock Agent activity without calling a provider."""

    log_signal = Signal(str, str)
    code_signal = Signal(str)
    diff_signal = Signal(str)
    status_signal = Signal(str, str)
    finished_signal = Signal(bool, str)

    _SIMULATED_LOGS: tuple[tuple[str, str], ...] = (
        ("已接收任务，准备分析工作区。", "info"),
        ("正在规划执行步骤……", "thinking"),
        ("模拟读取目标文件。", "info"),
        ("已生成候选代码修改。", "warning"),
        ("模拟运行测试：全部通过。", "success"),
        ("任务完成。", "success"),
    )

    def __init__(
        self,
        task: str,
        *,
        interval_ms: int = 500,
        total_ticks: int = 10,
    ) -> None:
        """Create a five-second-by-default worker with six log emissions."""

        super().__init__()
        if interval_ms < 0:
            raise ValueError("interval_ms must be non-negative")
        if total_ticks < len(self._SIMULATED_LOGS):
            raise ValueError("total_ticks must allow all simulated logs")
        self.task = task
        self.interval_ms = interval_ms
        self.total_ticks = total_ticks

    def run(self) -> None:
        """Emit six half-second logs while keeping a five-second lifecycle."""

        self.status_signal.emit("running", f"正在运行：{self.task}")
        for tick in range(self.total_ticks):
            if self.isInterruptionRequested():
                self.status_signal.emit("error", "模拟任务已取消")
                self.finished_signal.emit(False, "模拟任务已取消")
                return

            self.msleep(self.interval_ms)
            if tick < len(self._SIMULATED_LOGS):
                message, level = self._SIMULATED_LOGS[tick]
                self.log_signal.emit(message, level)
                if tick == 3:
                    self.code_signal.emit(
                        "def divide(a: float, b: float) -> float:\n"
                        "    if b == 0:\n"
                        "        raise ValueError(\"divisor cannot be zero\")\n"
                        "    return a / b\n"
                    )
                if tick == 4:
                    self.diff_signal.emit(
                        "--- a/calc.py\n"
                        "+++ b/calc.py\n"
                        "@@ -1,2 +1,4 @@\n"
                        " def divide(a, b):\n"
                        "+    if b == 0:\n"
                        "+        raise ValueError(\"divisor cannot be zero\")\n"
                        "     return a / b\n"
                    )

        self.status_signal.emit("ready", "模拟任务完成")
        self.finished_signal.emit(True, "模拟任务完成")
