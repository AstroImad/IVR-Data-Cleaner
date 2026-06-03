"""
Parses IVR script documents (PDF/DOCX) to extract:
- Questions (mapped to flow numbers)
- Answer choices (mapped to FlowNo_X=Y patterns)

Handles multi-layer/branching IVR scripts where:
- "Tekan X untuk Y Call flow N" indicates an answer that redirects to another flow
- "Tekan N hingga M" is a range description for multi-item sub-questions
- Duplicate question texts are disambiguated by appending the flow number
"""

import re
from typing import Dict, Tuple, Optional, List


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber."""
    import pdfplumber
    import io
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    from docx import Document
    import io
    doc = Document(io.BytesIO(file_bytes))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text


def clean_flow_line(text: str) -> str:
    """Fix multi-line 'Call flow' patterns like 'Call flow\n4' or 'Call \nflow 6'."""
    text = re.sub(r'Call\s*\n?\s*flow\s*\n?\s*(\d+)', r'Call flow \1', text)
    text = re.sub(r'Call flow\s*\n\s*(\d+)', r'Call flow \1', text)
    return text


def _disambiguate_questions(flow_to_question: Dict[int, str]) -> Dict[int, str]:
    """
    Ensure all question texts are unique by appending flow number to duplicates.
    """
    from collections import Counter
    q_counts = Counter(flow_to_question.values())
    duplicates = {q for q, c in q_counts.items() if c > 1}

    if not duplicates:
        return flow_to_question

    result = {}
    for flow_num, question in flow_to_question.items():
        if question in duplicates:
            result[flow_num] = f"{question} (Call flow {flow_num})"
        else:
            result[flow_num] = question
    return result


def parse_ivr_script(file_bytes: bytes, filename: str) -> Tuple[
    Dict[int, str],
    Dict[str, str]
]:
    """
    Parse an IVR script document to extract questions and answer mappings.

    Key insight: In multi-layer IVR, "Call flow N" appears both:
    1. As section boundary markers (after question text)
    2. As routing info embedded inside answer lines ("Tekan X untuk Y Call flow M")

    The parser handles this by:
    - Pre-processing: temporarily removing "Call flow M" from inside "Tekan" lines
    - Then splitting on the remaining standalone "Call flow N" markers
    - Answers from "Tekan X untuk Y Call flow M" lines still capture the answer text

    Args:
        file_bytes: Raw bytes of the uploaded file
        filename: Name of the file (used to detect format)

    Returns:
        flow_to_question: Mapping of flow number to question text
        flow_value_mapping: Mapping of FlowNo_X=Y to answer text
    """
    filename_lower = filename.lower()
    if filename_lower.endswith('.pdf'):
        text = extract_text_from_pdf(file_bytes)
    elif filename_lower.endswith('.docx') or filename_lower.endswith('.doc'):
        text = extract_text_from_docx(file_bytes)
    else:
        raise ValueError(f"Unsupported file format: {filename}")

    # Fix multi-line "Call flow" patterns
    text = clean_flow_line(text)

    # ── Regex patterns ───────────────────────────────────────────────────
    # "Tekan N untuk X Call flow M" - answer with redirect routing
    tekan_redirect_pattern = re.compile(
        r'(Tekan\s+\d+\s+untuk\s+.+?)\s+Call\s+flow\s+\d+',
        re.IGNORECASE
    )

    # "Tekan N untuk X" - standard answer (no redirect)
    tekan_untuk_pattern = re.compile(
        r'Tekan\s+(\d+)\s+untuk\s+(.+)',
        re.IGNORECASE
    )

    # "Tekan N hingga M" - range description in question text (NOT an answer)
    tekan_range_pattern = re.compile(r'Tekan\s+\d+\s+hingga\s+\d+', re.IGNORECASE)

    # Multi-item pattern: "Entity Name tekan N hingga M Call flow X"
    multi_item_pattern = re.compile(
        r'(.+?)\s+tekan\s+(\d+)\s+hingga\s+(\d+)\s+Call\s+flow\s+(\d+)',
        re.IGNORECASE
    )

    # Skip patterns for greeting/intro lines
    skip_patterns = [
        'salam sejahtera', 'terima kasih', 'kajian bebas', 'cpi',
        'hanya merangkumi', 'soalan untuk bukan pengundi',
        'berdasarkan', 'q3,q4', 'q6 jawab', 'q2'
    ]

    # ── Pre-process: Handle "Tekan N untuk X Call flow M" lines ─────────
    # These are answers WITH routing, not section boundaries.
    # Strategy: replace "Call flow M" in these lines with a placeholder
    # so they don't get treated as section boundaries.

    # Store redirect answers separately
    redirect_answers: Dict[str, str] = {}  # FlowNo_X=Y -> answer text

    def replace_redirect(match):
        """Replace 'Call flow M' in Tekan lines with a placeholder."""
        tekan_text = match.group(1)  # "Tekan N untuk X"
        tekan_match = tekan_untuk_pattern.search(tekan_text)
        if tekan_match:
            return tekan_text  # Remove "Call flow M" part
        return match.group(0)

    # Find all redirect lines and extract answers before modifying text
    for m in tekan_redirect_pattern.finditer(text):
        full_line = m.group(0)
        tekan_match = tekan_untuk_pattern.search(full_line)
        if tekan_match:
            choice_num = int(tekan_match.group(1))
            answer_text = tekan_match.group(2).strip()
            # Remove trailing "Call flow M" from answer text
            answer_text = re.sub(r'\s*Call\s+flow\s+\d+\s*$', '', answer_text, flags=re.IGNORECASE).strip()
            # We don't know the flow number yet, store temporarily
            # Will be assigned during section processing

    # Now replace "Call flow M" inside Tekan lines with empty string
    processed_text = tekan_redirect_pattern.sub(replace_redirect, text)

    # ── First pass: detect multi-item sub-questions ──────────────────────
    multi_item_questions: Dict[int, str] = {}

    for match in multi_item_pattern.finditer(processed_text):
        entity_name = match.group(1).strip()
        flow_num = int(match.group(4))
        multi_item_questions[flow_num] = entity_name

    # ── Second pass: split by standalone "Call flow N" and process ──────
    call_flow_pattern = re.compile(r'Call\s+flow\s+(\d+)', re.IGNORECASE)
    cf_matches = list(call_flow_pattern.finditer(processed_text))

    flow_to_question: Dict[int, str] = {}
    flow_value_mapping: Dict[str, str] = {}

    for idx, cf_match in enumerate(cf_matches):
        flow_num = int(cf_match.group(1))
        cf_end = cf_match.end()

        # Content BEFORE this "Call flow N" (from previous flow's end)
        if idx > 0:
            content_before = processed_text[cf_matches[idx - 1].end():cf_match.start()]
        else:
            content_before = processed_text[:cf_match.start()]

        # Content AFTER this "Call flow N" (until next "Call flow N")
        if idx + 1 < len(cf_matches):
            content_after = processed_text[cf_end:cf_matches[idx + 1].start()]
        else:
            content_after = processed_text[cf_end:]

        # ── Extract question text ──────────────────────────────────────
        if flow_num in multi_item_questions:
            question_text = multi_item_questions[flow_num]
        else:
            question_text = _extract_question_from_content(
                content_before, content_after,
                tekan_untuk_pattern, tekan_range_pattern, skip_patterns
            )

        if question_text and flow_num >= 2:
            flow_to_question[flow_num] = question_text

        # ── Extract answer mappings from content_after ─────────────────
        _extract_answers_from_content(
            content_after, flow_num,
            tekan_untuk_pattern, tekan_range_pattern,
            flow_value_mapping
        )

    # ── Disambiguate duplicate question texts ────────────────────────────
    flow_to_question = _disambiguate_questions(flow_to_question)

    return flow_to_question, flow_value_mapping


def _extract_question_from_content(
    content_before: str,
    content_after: str,
    tekan_untuk_pattern,
    tekan_range_pattern,
    skip_patterns: List[str]
) -> str:
    """Extract question text from content before and after a 'Call flow N' marker."""

    before_lines = content_before.strip().split('\n')
    question_parts = []

    for line in before_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Skip greeting/intro lines
        if any(skip in line_stripped.lower() for skip in skip_patterns):
            continue

        # Skip "Tekan N untuk X" lines (answers for the PREVIOUS flow)
        if tekan_untuk_pattern.search(line_stripped):
            continue

        # Skip "Tekan N hingga M" lines (range descriptions)
        if tekan_range_pattern.search(line_stripped):
            continue

        # Clean stray characters from line starts
        line_stripped = re.sub(r'^[\]\[\)\(}{\s]+', '', line_stripped).strip()
        if not line_stripped:
            continue

        question_parts.append(line_stripped)

    question_text = " ".join(question_parts).strip()

    # Also check for trailing question text AFTER "Call flow N" but BEFORE
    # the first "Tekan" answer line
    after_lines = content_after.strip().split('\n')
    trailing_question = []

    for line in after_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Stop at first "Tekan N untuk" line
        if tekan_untuk_pattern.search(line_stripped):
            break
        # Stop at "Tekan N hingga" range description
        if tekan_range_pattern.search(line_stripped):
            break
        # Skip greeting lines
        if any(skip in line_stripped.lower() for skip in skip_patterns):
            continue
        trailing_question.append(line_stripped)

    if trailing_question:
        trailing_text = " ".join(trailing_question).strip()
        if question_text:
            question_text = question_text.rstrip() + " " + trailing_text
        else:
            question_text = trailing_text

    return question_text


def _extract_answers_from_content(
    content_after: str,
    flow_num: int,
    tekan_untuk_pattern,
    tekan_range_pattern,
    flow_value_mapping: Dict[str, str]
):
    """Extract answer mappings from content after a 'Call flow N' marker."""
    after_lines = content_after.strip().split('\n')

    for line in after_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Check for "Tekan N hingga M" (range description - not an answer)
        if tekan_range_pattern.search(line_stripped):
            continue

        # Check for "Tekan N untuk X" (answer - may or may not have redirect)
        tekan_match = tekan_untuk_pattern.search(line_stripped)
        if tekan_match:
            choice_num = int(tekan_match.group(1))
            answer_text = tekan_match.group(2).strip()
            # Remove any trailing "Call flow M" that might remain
            answer_text = re.sub(r'\s*Call\s+flow\s+\d+\s*$', '', answer_text, flags=re.IGNORECASE).strip()
            key = f"FlowNo_{flow_num}={choice_num}"
            flow_value_mapping[key] = answer_text


def _clean_question_text(text: str, tekan_redirect_pattern) -> str:
    """
    Clean question text by removing embedded "Tekan N untuk X" redirect patterns.
    """
    lines = text.split('\n')
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # If this line has a "Tekan N untuk X" pattern, extract the part before it
        tekan_match = re.search(r'Tekan\s+\d+\s+untuk\s+', line, re.IGNORECASE)
        if tekan_match:
            before_tekan = line[:tekan_match.start()].strip()
            if before_tekan:
                cleaned_lines.append(before_tekan)
            continue

        cleaned_lines.append(line)

    return " ".join(cleaned_lines).strip()