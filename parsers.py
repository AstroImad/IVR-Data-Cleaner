"""
Parses IVR script documents (PDF/DOCX) to extract:
- Questions (mapped to flow numbers)
- Answer choices (mapped to FlowNo_X=Y patterns)

Handles multi-layer/branching IVR scripts where:
- "Tekan X untuk Y Call flow N" indicates a redirect (not a regular answer)
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
    E.g., if "Sila pilih negeri anda:" appears for flows 28, 29, 30, 31, 32,
    they become:
        "Sila pilih negeri anda: (Flow 28)"
        "Sila pilih negeri anda: (Flow 29)"
        etc.
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

    Handles multi-layer/branching IVR scripts where:
    - "Tekan X untuk Y Call flow N" indicates a redirect (not a regular answer)
    - "Tekan 1 hingga 3" is a range description in question text, not an answer
    - Multi-item sub-questions (e.g., "Bomba tekan 1 hingga 3 Call flow 5")
    - Duplicate question texts are disambiguated by appending flow number

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
    call_flow_pattern = re.compile(r'Call\s+flow\s+(\d+)', re.IGNORECASE)

    # "Tekan N untuk X" - standard answer format (must start the line)
    tekan_untuk_pattern = re.compile(r'^\s*Tekan\s+(\d+)\s+untuk\s+(.+)', re.IGNORECASE | re.MULTILINE)

    # "Tekan N untuk X Call flow M" - redirect format (branching, must start the line)
    tekan_redirect_pattern = re.compile(
        r'^\s*Tekan\s+(\d+)\s+untuk\s+(.+?)\s+Call\s+flow\s+\d+',
        re.IGNORECASE | re.MULTILINE
    )

    # "Tekan N hingga M" - range description in question text (NOT an answer)
    tekan_range_pattern = re.compile(r'Tekan\s+\d+\s+hingga\s+\d+', re.IGNORECASE)

    # Multi-item pattern: "Entity Name tekan N hingga M Call flow X"
    # This appears in sub-questions like "Bomba tekan 1 hingga 3 Call flow 5"
    multi_item_pattern = re.compile(
        r'(.+?)\s+tekan\s+(\d+)\s+hingga\s+(\d+)\s+Call\s+flow\s+(\d+)',
        re.IGNORECASE
    )

    # Skip patterns for greeting/intro lines
    skip_patterns = [
        'salam sejahtera', 'terima kasih', 'kajian bebas', 'cpi',
        'hanya merangkumi', 'soalan untuk bukan pengundi'
    ]

    # ── First pass: detect multi-item sub-questions ──────────────────────
    # Multi-item patterns like "Bomba tekan 1 hingga 3 Call flow 5"
    # These are entity names with their own flow numbers.
    multi_item_questions: Dict[int, str] = {}  # flow_num -> entity_name
    multi_item_flows: set = set()

    for match in multi_item_pattern.finditer(text):
        entity_name = match.group(1).strip()
        flow_num = int(match.group(4))
        multi_item_questions[flow_num] = entity_name
        multi_item_flows.add(flow_num)

    # ── Second pass: find all "Call flow N" and extract questions/answers ─
    cf_matches = list(call_flow_pattern.finditer(text))

    flow_to_question: Dict[int, str] = {}
    flow_value_mapping: Dict[str, str] = {}

    for idx, cf_match in enumerate(cf_matches):
        flow_num = int(cf_match.group(1))
        cf_start = cf_match.start()
        cf_end = cf_match.end()

        # Content BEFORE this "Call flow N" (from previous flow's end)
        if idx > 0:
            content_before = text[cf_matches[idx - 1].end():cf_start]
        else:
            content_before = text[:cf_start]

        # Content AFTER this "Call flow N" (until next "Call flow N")
        if idx + 1 < len(cf_matches):
            content_after = text[cf_end:cf_matches[idx + 1].start()]
        else:
            content_after = text[cf_end:]

        # ── Extract question text ──────────────────────────────────────
        # Check if this is a multi-item flow
        if flow_num in multi_item_questions:
            question_text = multi_item_questions[flow_num]
        else:
            question_text = _extract_question_from_content(
                content_before, content_after,
                tekan_untuk_pattern, tekan_range_pattern, skip_patterns
            )
            if question_text:
                question_text = _clean_question_text(
                    question_text, tekan_redirect_pattern
                )

        if question_text and flow_num >= 2:
            flow_to_question[flow_num] = question_text

        # ── Extract answer mappings from content_after ─────────────────
        _extract_answers_from_content(
            content_after, flow_num,
            tekan_untuk_pattern, tekan_redirect_pattern, tekan_range_pattern,
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

        # Skip "Tekan N untuk X" lines (these are answers for the PREVIOUS flow)
        if tekan_untuk_pattern.search(line_stripped):
            continue

        # Skip "Tekan N hingga M" lines (range descriptions for multi-item sub-questions)
        if tekan_range_pattern.search(line_stripped):
            continue

        # Clean stray characters from line starts (e.g., "]Sila pilih negeri anda:")
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
    tekan_redirect_pattern,
    tekan_range_pattern,
    flow_value_mapping: Dict[str, str]
):
    """Extract answer mappings from content after a 'Call flow N' marker."""
    after_lines = content_after.strip().split('\n')

    for line in after_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Check for "Tekan N untuk X Call flow M" (REDIRECT - not a real answer)
        if tekan_redirect_pattern.search(line_stripped):
            continue

        # Check for "Tekan N hingga M" (range description - not an answer)
        if tekan_range_pattern.search(line_stripped):
            continue

        # Check for "Tekan N untuk X" (regular answer)
        tekan_match = tekan_untuk_pattern.search(line_stripped)
        if tekan_match:
            choice_num = int(tekan_match.group(1))
            answer_text = tekan_match.group(2).strip()
            key = f"FlowNo_{flow_num}={choice_num}"
            flow_value_mapping[key] = answer_text


def _clean_question_text(text: str, tekan_redirect_pattern) -> str:
    """
    Clean question text by removing embedded "Tekan N untuk X" redirect patterns
    that appear on the same line as the question.

    Preserves "Tekan N hingga M" range descriptions.
    """
    lines = text.split('\n')
    cleaned_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # If this line has a redirect "Tekan N untuk X Call flow M",
        # extract the part before the Tekan pattern
        redirect_match = tekan_redirect_pattern.search(line)
        if redirect_match:
            before_tekan = line[:redirect_match.start()].strip()
            if before_tekan:
                cleaned_lines.append(before_tekan)
            continue

        # If this line has a "Tekan N untuk X" (without "Call flow"),
        # it's an answer line that got mixed into the question. Extract the question part.
        tekan_match = re.search(r'Tekan\s+\d+\s+untuk\s+', line, re.IGNORECASE)
        if tekan_match:
            before_tekan = line[:tekan_match.start()].strip()
            if before_tekan:
                cleaned_lines.append(before_tekan)
            continue

        # Keep the line as-is (includes "Tekan N hingga M" range descriptions)
        cleaned_lines.append(line)

    return " ".join(cleaned_lines).strip()