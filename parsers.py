"""
Parses IVR script documents (PDF/DOCX) to extract:
- Questions (mapped to flow numbers)
- Answer choices (mapped to FlowNo_X=Y patterns)
- Flow graph (redirect relationships between flows for skip logic handling)

Handles multi-layer/branching IVR scripts where:
- "Tekan X untuk Y Call flow N" indicates an answer that redirects to another flow
- "Tekan N hingga M" is a range description for multi-item sub-questions
- Duplicate question texts are disambiguated by appending the flow number
"""

import re
from typing import Dict, Tuple, Optional, List, Set


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


def _build_flow_graph(
    original_text: str,
    processed_text: str,
    cf_matches: list,
    tekan_untuk_pattern,
    tekan_range_pattern,
) -> Dict[int, Dict]:
    """
    Build a flow graph capturing redirect relationships between flows.

    Strategy: Iterate through the ORIGINAL text line by line. We identify
    section boundaries (standalone "Soalan X ... Call flow N" lines) and
    answer lines ("Tekan X untuk Y Call flow M" lines). Answer redirects
    are assigned to the most recently seen section boundary.

    This approach avoids position-mapping issues between original and
    processed text, and correctly handles all skip logic patterns.

    Args:
        original_text: Original text with all "Call flow" patterns intact
        processed_text: Text with redirect "Call flow M" stripped from Tekan lines
        cf_matches: Standalone "Call flow N" matches from processed_text
        tekan_untuk_pattern: Regex for "Tekan N untuk X"
        tekan_range_pattern: Regex for "Tekan N hingga M"

    Returns:
        Dict mapping flow_num to a dict with:
          - 'answer_redirects': Dict[int, int] mapping choice_num -> target_flow
          - 'is_answer_branch': bool, True if this flow is a destination of a redirect
    """
    # Collect standalone flow numbers from processed_text matches
    # (these are the section boundaries, not Tekan redirect lines)
    standalone_flow_nums = {int(m.group(1)) for m in cf_matches}

    # Scan original text line by line
    lines = original_text.split('\n')
    current_flow = None
    flow_graph: Dict[int, Dict] = {}
    all_redirect_targets: Set[int] = set()

    tekan_redirect_re = re.compile(
        r'Tekan\s+(\d+)\s+untuk\s+.+?\s+Call\s+flow\s+(\d+)',
        re.IGNORECASE
    )
    standalone_cf_re = re.compile(r'Call\s+flow\s+(\d+)', re.IGNORECASE)

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Check if this line is a "Tekan X untuk Y Call flow M" (answer with redirect)
        tekan_match = tekan_untuk_pattern.search(line_stripped)
        redirect_match = tekan_redirect_re.search(line_stripped)

        if tekan_match and redirect_match:
            # This is an answer line with a redirect
            choice_num = int(tekan_match.group(1))
            target_flow = int(redirect_match.group(2))
            all_redirect_targets.add(target_flow)

            if current_flow is not None:
                if current_flow not in flow_graph:
                    flow_graph[current_flow] = {'answer_redirects': {}, 'is_answer_branch': False}
                flow_graph[current_flow]['answer_redirects'][choice_num] = target_flow
            continue

        # Check if this line has a standalone "Call flow N" (section boundary)
        # It's standalone if it's NOT a "Tekan X untuk" line
        if not tekan_match:
            cf_match = standalone_cf_re.search(line_stripped)
            if cf_match:
                flow_num = int(cf_match.group(1))
                if flow_num in standalone_flow_nums:
                    current_flow = flow_num
                    if current_flow not in flow_graph:
                        flow_graph[current_flow] = {'answer_redirects': {}, 'is_answer_branch': False}

    # Mark redirect target flows as branch-specific
    for target_flow in all_redirect_targets:
        if target_flow in flow_graph:
            flow_graph[target_flow]['is_answer_branch'] = True

    return flow_graph


def _get_core_question_text(text: str) -> str:
    """Strip 'Soalan [ordinal].' prefix from question text."""
    stripped = re.sub(r'^Soalan\s+\w+(\s+\w+)*\.\s*', '', text, flags=re.IGNORECASE)
    return stripped.strip() if stripped.strip() else text.strip()


def _identify_branch_groups(
    flow_graph: Dict[int, Dict],
    flow_to_question: Dict[int, str],
    flow_value_mapping: Dict[str, str],
) -> List[List[int]]:
    """
    Identify groups of mutually exclusive branch flows using multiple strategies:

    Strategy 1: Flows with the same core question text (after stripping
    "Soalan N." prefix) are branches of the same question.

    Strategy 2: If a parent flow's answers redirect to multiple different
    flows, those target flows form a branch group.

    Strategy 3: If flows in an existing branch group each redirect ALL their
    options to a single (different) target, the targets form a downstream
    branch group. For example, if branch [4,5,6,7,8] exists, and flows
    4,5,6,8 all redirect to Flow 9 while Flow 7 redirects to Flow 10,
    then [9, 10] is a new branch group.

    Returns:
        List of lists, each inner list is a group of mutually exclusive flow numbers.
    """
    # Group by core question text
    core_to_flows: Dict[str, List[int]] = {}
    for flow_num, question in flow_to_question.items():
        core = _get_core_question_text(question)
        if core not in core_to_flows:
            core_to_flows[core] = []
        core_to_flows[core].append(flow_num)

    # Strategy 1: Branch groups from shared core question text
    branch_groups: List[List[int]] = []
    for core, flows in core_to_flows.items():
        if len(flows) >= 2:
            branch_groups.append(sorted(flows))

    # Strategy 2: Branch groups from parent redirect targets
    parent_redirect_groups: Dict[int, List[int]] = {}
    for flow_num, info in flow_graph.items():
        redirects = info.get('answer_redirects', {})
        if len(redirects) >= 2:
            targets = list(set(redirects.values()))
            if len(targets) >= 2:
                parent_redirect_groups[flow_num] = targets

    for parent, targets in parent_redirect_groups.items():
        sorted_targets = sorted(targets)
        merged = False
        for existing in branch_groups:
            if len(set(existing) & set(sorted_targets)) >= 2:
                existing_set = set(existing) | set(sorted_targets)
                branch_groups[branch_groups.index(existing)] = sorted(existing_set)
                merged = True
                break
        if not merged:
            branch_groups.append(sorted_targets)

    # Strategy 3: Downstream branch groups from merge-point analysis.
    # For each existing branch group, check if flows in the group redirect
    # to different single targets. Those targets form a new branch group.
    new_groups: List[List[int]] = []
    for group in branch_groups:
        # For each flow in the group, find where it redirects ALL its options
        merge_targets: Dict[int, List[int]] = {}  # merge_target -> list of source flows
        for flow_num in group:
            if flow_num not in flow_graph:
                continue
            redirects = flow_graph[flow_num].get('answer_redirects', {})
            if not redirects:
                continue
            # Check if ALL options redirect to the SAME single target
            unique_targets = set(redirects.values())
            if len(unique_targets) == 1:
                target = list(unique_targets)[0]
                if target not in merge_targets:
                    merge_targets[target] = []
                merge_targets[target].append(flow_num)

        # If there are multiple distinct merge targets, they form a branch group
        if len(merge_targets) >= 2:
            downstream_group = sorted(merge_targets.keys())
            new_groups.append(downstream_group)

    # Add new downstream groups
    for new_group in new_groups:
        # Check if it overlaps with existing groups
        merged = False
        for existing in branch_groups:
            if len(set(existing) & set(new_group)) >= 2:
                existing_set = set(existing) | set(new_group)
                branch_groups[branch_groups.index(existing)] = sorted(existing_set)
                merged = True
                break
        if not merged:
            branch_groups.append(new_group)

    # Deduplicate
    unique_groups = []
    seen = set()
    for group in branch_groups:
        key = tuple(sorted(group))
        if key not in seen:
            seen.add(key)
            unique_groups.append(sorted(group))

    return unique_groups


def parse_ivr_script(file_bytes: bytes, filename: str) -> Tuple[
    Dict[int, str],
    Dict[str, str],
    Dict[int, Dict],
    List[List[int]],
]:
    """
    Parse an IVR script document to extract questions, answer mappings,
    flow graph, and branch groups.

    Args:
        file_bytes: Raw bytes of the uploaded file
        filename: Name of the file (used to detect format)

    Returns:
        flow_to_question: Mapping of flow number to question text
        flow_value_mapping: Mapping of FlowNo_X=Y to answer text
        flow_graph: Mapping of flow number to flow info dict
        branch_groups: List of mutually exclusive flow groups
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

    # Skip patterns for greeting/intro/instruction lines
    skip_patterns = [
        'salam sejahtera', 'terima kasih', 'kajian bebas', 'cpi',
        'hanya merangkumi', 'soalan untuk bukan pengundi',
        'berdasarkan', 'jawab soalan', 'jawab ini',
    ]

    # ── Pre-process: Handle "Tekan N untuk X Call flow M" lines ─────────
    # These are answers WITH routing, not section boundaries.
    # Strategy: replace "Call flow M" in these lines with a placeholder
    # so they don't get treated as section boundaries.

    def replace_redirect(match):
        """Replace 'Call flow M' in Tekan lines with a placeholder."""
        tekan_text = match.group(1)  # "Tekan N untuk X"
        tekan_match = tekan_untuk_pattern.search(tekan_text)
        if tekan_match:
            return tekan_text  # Remove "Call flow M" part
        return match.group(0)

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

    # Build flow graph from ORIGINAL text (before preprocessing strips redirects)
    flow_graph = _build_flow_graph(
        text, processed_text, cf_matches, tekan_untuk_pattern, tekan_range_pattern
    )

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

    # ── Identify branch groups ───────────────────────────────────────────
    branch_groups = _identify_branch_groups(
        flow_graph, flow_to_question, flow_value_mapping
    )

    return flow_to_question, flow_value_mapping, flow_graph, branch_groups


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

        # Skip greeting/intro/instruction lines
        line_lower = line_stripped.lower()
        if any(skip in line_lower for skip in skip_patterns):
            continue

        # Skip lines that look like instructions (Q2 OPTION 2, Q3,Q4,Q5, etc.)
        if re.match(r'^Q\d+', line_stripped, re.IGNORECASE):
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
        # Skip greeting/instruction lines
        line_lower = line_stripped.lower()
        if any(skip in line_lower for skip in skip_patterns):
            continue
        if re.match(r'^Q\d+', line_stripped, re.IGNORECASE):
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