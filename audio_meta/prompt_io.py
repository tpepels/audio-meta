from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol


class PromptIO(Protocol):
    def print(self, text: str = "") -> None: ...

    def input(self, prompt: str = "") -> str: ...


class ConsolePromptIO:
    def print(self, text: str = "") -> None:
        print(text)

    def input(self, prompt: str = "") -> str:
        return input(prompt)


@dataclass(slots=True)
class BufferPromptIO:
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)

    def print(self, text: str = "") -> None:
        self.outputs.append(text)

    def input(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.inputs:
            raise AssertionError("BufferPromptIO has no more inputs")
        return self.inputs.pop(0)

