import subprocess
from collections.abc import Callable
from pathlib import Path

from media_factory.domain.narration import AudioPolicy

Runner = Callable[..., subprocess.CompletedProcess[str]]


class MasterProcessingError(RuntimeError):
    code = "master_processing_failed"

    def __init__(self, message: str, command: list[str]) -> None:
        super().__init__(message)
        self.command = command


class FFmpegMasterAssembler:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", runner: Runner = subprocess.run) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.runner = runner

    def build(
        self,
        *,
        source: Path,
        destination: Path,
        policy: AudioPolicy,
        source_duration: float,
        audio_track: Path | None,
    ) -> list[str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [self.ffmpeg_bin, "-v", "error", "-n", "-i", str(source)]
        if policy in {AudioPolicy.REPLACE, AudioPolicy.ADDITIONAL}:
            if audio_track is None:
                raise MasterProcessingError("audio policy requires a selected track", command)
            command.extend(["-i", str(audio_track)])

        command.extend(["-map", "0:v:0"])
        if policy is AudioPolicy.PRESERVE:
            command.extend(["-map", "0:a?", "-c:v", "copy", "-c:a", "copy"])
        elif policy is AudioPolicy.REMOVE:
            command.extend(["-an", "-c:v", "copy"])
        elif policy is AudioPolicy.REPLACE:
            command.extend(["-map", "1:a:0", "-c:v", "copy", "-c:a", "aac"])
        else:
            command.extend(["-map", "0:a?", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac"])
        command.extend(
            [
                "-t",
                f"{source_duration:.6f}",
                "-map_metadata",
                "0",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
        try:
            result = self.runner(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            destination.unlink(missing_ok=True)
            raise MasterProcessingError("ffmpeg is not installed or not on PATH", command) from exc
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise MasterProcessingError(
                result.stderr.strip() or "master assembly failed",
                command,
            )
        return command


class FFmpegDecodeValidator:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", runner: Runner = subprocess.run) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.runner = runner

    def validate(self, master: Path) -> list[str]:
        command = [
            self.ffmpeg_bin,
            "-v",
            "error",
            "-i",
            str(master),
            "-map",
            "0",
            "-f",
            "null",
            "-",
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise MasterProcessingError("ffmpeg is not installed or not on PATH", command) from exc
        if result.returncode != 0:
            raise MasterProcessingError(
                result.stderr.strip() or "master decode validation failed",
                command,
            )
        return command
