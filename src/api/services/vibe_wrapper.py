import subprocess
import os
import re
import tempfile
import shutil
from pathlib import Path
from typing import Iterator, Optional
from dataclasses import dataclass, field


@dataclass
class VibeResult:
    """Result from running vibe."""

    success: bool
    output: str
    files_changed: list[str] = field(default_factory=list)
    error: Optional[str] = None


class VibeInteractiveWrapper:
    """
    Interactive wrapper around Mistral Vibe CLI.

    This wrapper manages long-running sessions with state preservation.
    It can handle task delegation and input requests by:
    1. Running vibe with a prompt
    2. Detecting if more information is needed
    3. Continuing the session with user input
    """

    def __init__(self, working_dir: Optional[str] = None, max_turns: int = 50):
        self.working_dir = working_dir or tempfile.mkdtemp(prefix="vibe_work_")
        self.max_turns = max_turns
        self.session_history: list[dict] = []
        self.current_turn = 0

    def _run_vibe(
        self, prompt: str, output_format: str = "text", continue_session: bool = False
    ) -> tuple[str, int]:
        """Run vibe CLI and return output."""
        cmd = ["vibe", "--prompt", prompt, "--output", output_format]

        if continue_session and self.session_history:
            # Continue from previous session
            cmd.append("--continue")

        # Ensure working directory exists
        os.makedirs(self.working_dir, exist_ok=True)

        # Initialize git if not exists (vibe needs git)
        git_dir = Path(self.working_dir) / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.working_dir, capture_output=True)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout per turn
            )
            return result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "Error: Vibe execution timed out", 1
        except Exception as e:
            return f"Error running vibe: {str(e)}", 1

    def _parse_output(self, output: str, output_format: str) -> VibeResult:
        """Parse vibe output to extract results and detect questions."""
        result = VibeResult(success=True, output=output)

        # Check for errors in output
        if "error" in output.lower() or "Traceback" in output:
            result.error = output

        # Extract file changes
        if "File created:" in output or "file created" in output.lower():
            files = re.findall(r"File created:\s*`?([^`\n]+)", output)
            result.files_changed.extend(files)

        return result

    def start_task(self, prompt: str) -> Iterator[VibeResult]:
        """
        Start a new task and yield results.

        If vibe needs input, it will yield a result with needs_input=True.
        The caller should then call continue_task() with the user's response.
        """
        # Build the full prompt with context about working directory
        full_prompt = self._build_prompt(prompt)

        output, returncode = self._run_vibe(full_prompt, output_format="text")

        result = self._parse_output(output, "text")
        result.success = returncode == 0

        # Store in history
        self.session_history.append(
            {
                "turn": self.current_turn,
                "prompt": prompt,
                "output": output,
                "success": result.success,
            }
        )
        self.current_turn += 1

        yield result

        # If we haven't reached max turns and more work might be needed,
        # we could continue automatically or wait for user input
        while self.current_turn < self.max_turns and not result.needs_input:
            # Check if the task seems complete
            if self._is_task_complete(output):
                break

            # Auto-continue with a follow-up prompt
            follow_up = "Continue with the implementation."
            output, returncode = self._run_vibe(
                follow_up, output_format="text", continue_session=True
            )

            result = self._parse_output(output, "text")
            result.success = returncode == 0

            self.session_history.append(
                {
                    "turn": self.current_turn,
                    "prompt": follow_up,
                    "output": output,
                    "success": result.success,
                }
            )
            self.current_turn += 1

            yield result

    def continue_task(self, user_response: str) -> Iterator[VibeResult]:
        """
        Continue a task with user input.

        This sends the user's response to vibe and continues the session.
        """
        output, returncode = self._run_vibe(
            user_response, output_format="text", continue_session=True
        )

        result = self._parse_output(output, "text")
        result.success = returncode == 0

        self.session_history.append(
            {
                "turn": self.current_turn,
                "prompt": f"[User response]: {user_response}",
                "output": output,
                "success": result.success,
            }
        )
        self.current_turn += 1

        yield result

    def _build_prompt(self, user_prompt: str) -> str:
        """Build the full prompt with context."""
        # Include information about the working directory
        context = f"""Working in directory: {self.working_dir}

User request: {user_prompt}

Important: 
- Clone any repositories you need into this working directory
- Create new branches for changes
- Make commits as you progress
- All file operations should happen in {self.working_dir} or subdirectories
"""
        return context

    def _is_task_complete(self, output: str) -> bool:
        """Heuristic to detect if vibe considers the task complete."""
        completion_indicators = [
            "task completed",
            "done",
            "finished",
            "created successfully",
            "completed successfully",
        ]
        output_lower = output.lower()
        return any(indicator in output_lower for indicator in completion_indicators)

    def get_working_directory(self) -> str:
        """Get the working directory path."""
        return self.working_dir
