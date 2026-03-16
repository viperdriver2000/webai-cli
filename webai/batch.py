"""Batch prompt file parser for sequential image generation."""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BatchPrompt:
    filename: str
    prompt: str
    note: str = ""
    ref_image: str = ""   # optional reference image path


@dataclass
class BatchFile:
    intro: str              # Full intro text (everything before first ### prompt)
    style_prefix: str       # First code block (also part of intro)
    prompts: list[BatchPrompt]


def parse_prompt_file(path: Path) -> BatchFile:
    """Parse a markdown prompt file.

    Expected format:
    - Everything before the first ### image header = intro (sent as context)
    - First code block within intro = style prefix (also prepended to each prompt)
    - ### filename.png headers followed by code blocks = individual prompts
    - Optional blockquote lines before a code block = notes

    Returns BatchFile with intro, style_prefix, and prompts.
    """
    text = path.read_text()
    lines = text.split("\n")

    style_prefix = ""
    prompts: list[BatchPrompt] = []

    # Find where the first image-related section starts
    # (the ## heading that contains the first ### image header)
    first_prompt_line = len(lines)
    for idx, line in enumerate(lines):
        if re.match(r"^###\s+\S+\.(?:png|jpg|jpeg|webp)", line, re.IGNORECASE):
            first_prompt_line = idx
            break

    # Walk back past the ### header to find the parent ## section header
    intro_end = first_prompt_line
    for idx in range(first_prompt_line - 1, -1, -1):
        line = lines[idx].strip()
        if line.startswith("## "):
            intro_end = idx
            break
        elif line and line != "---":
            # Non-empty, non-separator line = end of intro content
            intro_end = idx + 1
            break

    # Strip trailing empty lines and separators
    while intro_end > 0 and lines[intro_end - 1].strip() in ("", "---"):
        intro_end -= 1
    intro = "\n".join(lines[:intro_end]).strip()

    # Extract first code block as style prefix
    first_block = re.search(r"^```\w*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    if first_block:
        style_prefix = first_block.group(1).strip()

    # Find all ### headers with filenames, then their code blocks
    current_filename = ""
    current_note = ""
    current_ref = ""
    in_header_section = False

    i = first_prompt_line
    while i < len(lines):
        line = lines[i]

        # Match ### headers with image filenames, optional [ref: path]
        header_match = re.match(
            r"^###\s+(\S+\.(?:png|jpg|jpeg|webp))(?:\s+\[ref:\s*(.+?)\])?",
            line, re.IGNORECASE
        )
        if header_match:
            current_filename = header_match.group(1)
            current_ref = header_match.group(2) or ""
            current_note = ""
            in_header_section = True
            i += 1
            continue

        # Collect blockquote notes after header
        if in_header_section and line.startswith(">"):
            current_note = line.lstrip("> ").strip()
            i += 1
            continue

        # Match code block after a header
        if in_header_section and line.startswith("```"):
            # Read until closing ```
            block_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block_lines.append(lines[i])
                i += 1
            prompt_text = "\n".join(block_lines).strip()

            if current_filename:
                prompts.append(BatchPrompt(
                    filename=current_filename,
                    prompt=prompt_text,
                    note=current_note,
                    ref_image=current_ref,
                ))
            current_filename = ""
            current_note = ""
            current_ref = ""
            in_header_section = False
            i += 1
            continue

        # Non-header ## lines reset state
        if line.startswith("## ") and not line.startswith("### "):
            in_header_section = False

        i += 1

    return BatchFile(intro=intro, style_prefix=style_prefix, prompts=prompts)
