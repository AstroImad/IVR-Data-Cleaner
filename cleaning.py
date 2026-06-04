"""
IVR data loading and cleaning logic.
Handles loading CSV files from Google Drive and cleaning the data.

Supports generalized IVR skip logic handling:
- Simple skip logic (e.g., Johor: Q6 → different next question)
- Multi-layer skip logic (e.g., Penang: Q2 branches into different question sets)
- Branch-aware completeness checking (respondents aren't penalized for branch-specific NaN)
"""

import os
import re
import io
import zipfile
import tempfile
import requests
import gdown
import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Tuple


def extract_gdrive_folder_id(folder_url: str) -> str:
    """Extract folder ID from a Google Drive URL."""
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', folder_url)
    if not match:
        raise ValueError(
            "Invalid Google Drive folder URL. "
            "Please provide a shared folder link like: https://drive.google.com/drive/folders/..."
        )
    return match.group(1)


def extract_gdrive_file_id(url: str) -> Optional[str]:
    """Extract file ID from a Google Drive file URL."""
    # Pattern: /file/d/FILE_ID/...
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    # Pattern: id=FILE_ID
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    # Pattern: /open?id=FILE_ID
    match = re.search(r'/open\?id=([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    return None


def download_single_gdrive_file(file_id: str) -> Optional[bytes]:
    """
    Download a single file from Google Drive using gdown.
    Returns file content as bytes.
    """
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as tmp:
            tmp_path = tmp.name
        
        gdown.download(url, tmp_path, quiet=True)
        
        with open(tmp_path, 'rb') as f:
            content = f.read()
        
        os.unlink(tmp_path)
        return content
    except Exception as e:
        print(f"Error downloading file {file_id}: {str(e)}")
        return None


def load_csv_from_gdrive_links(text_input: str) -> Tuple[pd.DataFrame, int]:
    """
    Load CSV files from Google Drive file links.
    The user can paste multiple file links (one per line).

    Args:
        text_input: Newline-separated Google Drive file URLs

    Returns:
        Tuple of (combined DataFrame, number of files loaded)
    """
    lines = [line.strip() for line in text_input.strip().split('\n') if line.strip()]
    
    dfs = []
    for line in lines:
        file_id = extract_gdrive_file_id(line)
        if not file_id:
            continue
        
        file_bytes = download_single_gdrive_file(file_id)
        if file_bytes:
            df = load_csv_file(file_bytes, source_name=line)
            if df is not None:
                dfs.append(df)
    
    if not dfs:
        raise ValueError(
            "Could not load any CSV files from the provided links. "
            "Make sure the links are Google Drive file links shared as 'Anyone with the link'."
        )
    
    combined = pd.concat(dfs, ignore_index=True)
    return combined, len(dfs)


def load_csv_file(file_path_or_bytes, source_name: str = "unknown") -> Optional[pd.DataFrame]:
    """
    Load a CSV file into a pandas DataFrame.
    Applies IVR-specific cleaning: select PhoneNo + UserKeyPress onwards columns,
    drop nulls, clean keypress values.

    Args:
        file_path_or_bytes: Either a file path (str) or file bytes
        source_name: Name of the source file (for logging)

    Returns:
        Cleaned DataFrame or None if error
    """
    try:
        if isinstance(file_path_or_bytes, bytes):
            f = io.StringIO(file_path_or_bytes.decode('utf-8', errors='replace'))
        else:
            f = open(file_path_or_bytes, 'r')

        df = pd.read_csv(f, skiprows=1, names=range(50), engine='python')

        if not isinstance(file_path_or_bytes, bytes):
            f.close()

        # Drop empty columns
        df.dropna(axis='columns', how='all', inplace=True)

        # Set first row as header
        df.columns = df.iloc[0]

        # Select PhoneNo column and all columns from UserKeyPress onwards
        if 'PhoneNo' not in df.columns:
            print(f"Warning: No 'PhoneNo' column found in {source_name}")
            return None

        phonenum = df[['PhoneNo']]

        if 'UserKeyPress' not in df.columns:
            print(f"Warning: No 'UserKeyPress' column found in {source_name}")
            return None

        keypress = df.loc[:, 'UserKeyPress':]
        raw_results = pd.concat([phonenum, keypress], axis='columns')

        # Only drop rows where ALL question columns are null (respondent didn't answer anything)
        # Keep rows with partial answers (e.g., skip logic, early hang-up)
        question_cols = [c for c in raw_results.columns if c != 'PhoneNo']
        raw_results = raw_results.dropna(subset=question_cols, how='all')

        # Convert to string
        raw_results = raw_results.astype(str)

        # Replace no-keypress with blank
        raw_results = raw_results.apply(
            lambda x: x.str.replace(r'FlowNo_\d{1,}=$', '', regex=True)
        )

        # Rename columns: PhoneNo -> phonenum, others -> numeric index
        new_columns = ['phonenum'] + list(range(len(raw_results.columns) - 1))
        raw_results.columns = new_columns

        return raw_results

    except Exception as e:
        print(f"Error loading file {source_name}: {str(e)}")
        return None


def download_gdrive_file(file_id: str, filename: str) -> Optional[bytes]:
    """
    Download a single file from Google Drive using gdown.

    Args:
        file_id: Google Drive file ID
        filename: Name of the file (for logging)

    Returns:
        File content as bytes, or None if error
    """
    import tempfile
    try:
        url = f"https://drive.google.com/uc?id={file_id}"
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=True) as tmp:
            gdown.download(url, tmp.name, quiet=True)
            with open(tmp.name, 'rb') as f:
                return f.read()
    except Exception as e:
        print(f"Error downloading {filename} (ID: {file_id}): {str(e)}")
        return None


def load_all_csvs_from_folder(folder_url: str) -> Tuple[pd.DataFrame, int]:
    """
    Load and combine all CSV files from a Google Drive shared folder.

    Args:
        folder_url: Google Drive shared folder URL

    Returns:
        Tuple of (combined DataFrame, number of files loaded)
    """
    try:
        files = list_gdrive_folder_files(folder_url)
    except Exception as e:
        raise ValueError(f"Could not access Google Drive folder: {str(e)}")

    if not files:
        raise ValueError(
            "No CSV files found in the Google Drive folder. "
            "Make sure the folder contains .csv files and is shared as 'Anyone with the link'."
        )

    dfs = []
    for file_info in files:
        df = load_csv_file(file_info['path'], source_name=file_info['name'])
        if df is not None:
            dfs.append(df)

    if not dfs:
        raise ValueError("Could not load any CSV files from the folder.")

    combined = pd.concat(dfs, ignore_index=True)
    return combined, len(dfs)


def load_all_csvs_from_bytes(file_list: list) -> pd.DataFrame:
    """
    Load and combine CSV files from a list of uploaded file objects.

    Args:
        file_list: List of uploaded file objects (from st.file_uploader)

    Returns:
        Combined DataFrame
    """
    dfs = []
    for uploaded_file in file_list:
        bytes_data = uploaded_file.read()
        df = load_csv_file(bytes_data, source_name=uploaded_file.name)
        if df is not None:
            dfs.append(df)

    if not dfs:
        raise ValueError("Could not load any of the uploaded CSV files.")

    combined = pd.concat(dfs, ignore_index=True)
    return combined


def load_csvs_from_zip(zip_bytes: bytes) -> Tuple[pd.DataFrame, int, List[str]]:
    """
    Load and combine all CSV files from a ZIP archive.

    Args:
        zip_bytes: Raw bytes of the uploaded ZIP file

    Returns:
        Tuple of (combined DataFrame, number of CSV files loaded, list of file names)
    """
    dfs = []
    file_names = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        csv_files = [f for f in zf.namelist() if f.lower().endswith('.csv') and not f.startswith('__MACOSX')]

        if not csv_files:
            raise ValueError("No CSV files found inside the ZIP archive.")

        for csv_name in csv_files:
            try:
                file_bytes = zf.read(csv_name)
                df = load_csv_file(file_bytes, source_name=csv_name)
                if df is not None:
                    dfs.append(df)
                    file_names.append(os.path.basename(csv_name))
            except Exception as e:
                print(f"Error loading {csv_name} from ZIP: {str(e)}")
                continue

    if not dfs:
        raise ValueError("Could not load any CSV files from the ZIP archive.")

    combined = pd.concat(dfs, ignore_index=True)
    return combined, len(dfs), file_names


def detect_flow_columns(df: pd.DataFrame) -> Dict[int, List[int]]:
    """
    Detect which columns contain FlowNo_X=Y patterns and map flow numbers to column indices.

    Args:
        df: DataFrame with numeric column names (except 'phonenum')

    Returns:
        Dict mapping flow_number -> list of column indices that contain that flow
    """
    flow_pattern = re.compile(r'FlowNo_(\d+)')
    flow_to_cols: Dict[int, List[int]] = {}

    for col in df.columns:
        if col == 'phonenum':
            continue
        # Check values in this column for FlowNo patterns
        sample_values = df[col].dropna().astype(str).head(50)
        for val in sample_values:
            match = flow_pattern.search(val)
            if match:
                flow_num = int(match.group(1))
                if flow_num not in flow_to_cols:
                    flow_to_cols[flow_num] = []
                if col not in flow_to_cols[flow_num]:
                    flow_to_cols[flow_num].append(col)
                break  # One match is enough to identify the column

    return flow_to_cols


def _get_core_question(question_text: str) -> str:
    """
    Extract the core question text by stripping common prefixes like
    "Soalan pertama.", "Soalan kedua.", "Soalan ketiga.", etc.
    
    This allows merging columns that have the same core question
    but different question number prefixes from different flows.
    
    Examples:
        "Soalan ketiga. Di parlimen manakah anda?" → "Di parlimen manakah anda?"
        "Soalan keempat. Di parlimen manakah anda?" → "Di parlimen manakah anda?"
        "Soalan kelapan. Sudikah anda mengundi?" → "Sudikah anda mengundi?"
        "Di parlimen manakah anda?" → "Di parlimen manakah anda?" (no prefix, unchanged)
    """
    # Strip "Soalan [ordinal]." prefix pattern
    # Matches: "Soalan pertama.", "Soalan kedua.", "Soalan ketiga belas.", etc.
    stripped = re.sub(
        r'^Soalan\s+\w+(\s+\w+)*\.\s*',
        '',
        question_text,
        flags=re.IGNORECASE
    )
    # If stripping removed something, return the stripped version
    # Otherwise return the original
    return stripped.strip() if stripped.strip() else question_text.strip()


def apply_column_renames(
    df: pd.DataFrame,
    flow_to_question: Dict[int, str],
    flow_to_cols: Dict[int, List[int]],
    branch_groups: List[List[int]] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Rename DataFrame columns using the question text from the parsed script.
    Then merge columns that have the same core question text (after stripping
    "Soalan N." prefixes), AND merge columns within the same branch group
    that map to equivalent questions.

    This handles multi-layer branching IVR where the same sub-question
    (e.g., "Di parlimen manakah anda?") appears across multiple flows
    (Flow 4, 5, 6, 7, 8) with different prefixes — each respondent
    only answered ONE of them, so merging produces a single column
    with all answers coalesced.

    Additionally, for branch groups where flows have DIFFERENT questions
    but are mutually exclusive (e.g., Flow 9 Q8 + Flow 10 Q9 in Johor),
    we do NOT merge those — they remain as separate columns.

    Args:
        df: DataFrame with numeric column names
        flow_to_question: Mapping from flow number to question text
        flow_to_cols: Mapping from flow number to column indices
        branch_groups: List of mutually exclusive flow groups (from parser)

    Returns:
        Tuple of (renamed & merged DataFrame, mapping dict {old_col_name: new_name})
    """
    rename_map = {}

    for flow_num, question in flow_to_question.items():
        if flow_num in flow_to_cols:
            for col_idx in flow_to_cols[flow_num]:
                rename_map[col_idx] = question

    df_renamed = df.rename(columns=rename_map)

    # Build a mapping: original_col_name -> core_question
    col_names = list(df_renamed.columns)
    col_to_core: Dict[str, str] = {}
    for col_name in col_names:
        col_str = str(col_name)
        if col_str == 'phonenum':
            col_to_core[col_str] = col_str
        else:
            col_to_core[col_str] = _get_core_question(col_str)

    # Group columns by their core question text
    core_groups: Dict[str, List[int]] = {}  # core_question -> list of column positions
    for pos, col_name in enumerate(col_names):
        col_str = str(col_name)
        core = col_to_core[col_str]
        if core not in core_groups:
            core_groups[core] = []
        core_groups[core].append(pos)

    # Build merged DataFrame
    result_cols = {}
    seen_cores = set()

    for col_name in col_names:
        col_str = str(col_name)
        core = col_to_core[col_str]
        if core in seen_cores:
            continue
        seen_cores.add(core)

        positions = core_groups[core]

        if len(positions) == 1:
            # No merging needed - use the original column name
            result_cols[col_str] = df_renamed.iloc[:, positions[0]].copy()
        else:
            # Merge: coalesce values across columns with same core question.
            # IMPORTANT: data may contain string 'nan'/'NaN'/'None'/''
            # instead of actual np.nan. fillna only works on actual NaN.
            # So we must first convert null-like strings to np.nan.
            null_like = {'', ' ', '  ', 'nan', 'NaN', 'NAN', 'None', 'none',
                         'NONE', 'null', 'NULL', 'NaT', 'nat', '<NA>'}

            # Start with first column, convert null-likes to NaN
            merged = df_renamed.iloc[:, positions[0]].copy()
            merged = merged.apply(
                lambda x: np.nan if (isinstance(x, str) and x.strip() in null_like) else x
            )

            # Coalesce remaining columns
            for pos in positions[1:]:
                next_col = df_renamed.iloc[:, pos].copy()
                next_col = next_col.apply(
                    lambda x: np.nan if (isinstance(x, str) and x.strip() in null_like) else x
                )
                merged = merged.fillna(next_col)

            result_cols[core] = merged

    df_merged = pd.DataFrame(result_cols)
    return df_merged, rename_map


def _classify_columns(
    df: pd.DataFrame,
    branch_groups: List[List[int]],
    flow_to_question: Dict[int, str],
) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """
    Classify DataFrame columns into common and branch-specific groups.

    Uses the branch_groups from the parser to determine which columns
    are mutually exclusive branches.

    Args:
        df: DataFrame with renamed columns
        branch_groups: List of mutually exclusive flow groups
        flow_to_question: Mapping from flow number to question text

    Returns:
        Tuple of (common_cols, branch_specific_cols, branch_col_groups)
        - common_cols: columns answered by all respondents on main path
        - branch_specific_cols: columns that are branch-specific
        - branch_col_groups: Dict mapping group_key -> list of column names
          (respondents need at least one non-null in each group)
    """
    def _get_core(text: str) -> str:
        stripped = re.sub(r'^Soalan\s+\w+(\s+\w+)*\.\s*', '', text, flags=re.IGNORECASE)
        return stripped.strip() if stripped.strip() else text.strip()

    question_cols = [c for c in df.columns if c not in ['phonenum', 'Mode']]

    # Build set of branch-specific core questions
    branch_core_questions: Dict[str, List[str]] = {}  # group_key -> list of col names

    if branch_groups:
        for group_idx, group in enumerate(branch_groups):
            # Get core questions for each flow in this group
            group_cores = set()
            for flow_num in group:
                if flow_num in flow_to_question:
                    core = _get_core(flow_to_question[flow_num])
                    group_cores.add(core)

            # Find DataFrame columns that match these core questions
            group_cols = []
            for col in question_cols:
                col_core = _get_core(str(col))
                if col_core in group_cores:
                    group_cols.append(col)

            if len(group_cols) >= 2:
                group_key = f"branch_group_{group_idx}"
                branch_core_questions[group_key] = group_cols

    # Classify columns
    branch_specific_cols = set()
    for group_key, cols in branch_core_questions.items():
        branch_specific_cols.update(cols)

    common_cols = [c for c in question_cols if c not in branch_specific_cols]
    branch_specific_list = list(branch_specific_cols)

    return common_cols, branch_specific_list, branch_core_questions


def apply_flow_value_mapping(
    df: pd.DataFrame,
    flow_value_mapping: Dict[str, str]
) -> pd.DataFrame:
    """
    Replace FlowNo_X=Y values with actual answer text throughout the DataFrame.

    Args:
        df: DataFrame containing FlowNo_X=Y values
        flow_value_mapping: Mapping from FlowNo_X=Y to answer text

    Returns:
        DataFrame with replaced values
    """
    df_mapped = df.replace(flow_value_mapping)
    return df_mapped


def detect_screening_flows(df: pd.DataFrame) -> List[Dict]:
    """
    Automatically detect screening/filter flows from the data.
    
    A screening flow is a column where respondents choose between options
    that lead to different paths (e.g., "Are you a voter?" → Ya/Tidak).
    
    Detection heuristics:
    1. Find columns with FlowNo values
    2. Check if one value has significantly fewer responses than others
    3. Check if respondents with that value have many empty columns
       (indicating they were redirected away from the main survey)
    
    Returns:
        List of dicts with keys:
        - 'col': column name
        - 'skip_value': the FlowNo value that triggers skip (e.g., 'FlowNo_2=2')
        - 'skip_label': description (e.g., 'Flow 2, Option 2')
        - 'main_count': number of main respondents
        - 'skip_count': number of skipped respondents
    """
    flow_pattern = re.compile(r'^FlowNo_(\d+)=(\d+)$')
    screening_flows = []
    
    for col in df.columns:
        if col == 'phonenum':
            continue
        
        col_str = df[col].astype(str).str.strip()
        
        # Find all unique FlowNo values in this column
        flowno_values = [v for v in col_str.unique() if flow_pattern.match(str(v))]
        
        if len(flowno_values) < 2:
            continue
        
        # For each FlowNo value, check if respondents with that value
        # have significantly more empty columns (indicating skip/redirect)
        total_cols = len([c for c in df.columns if c != 'phonenum'])
        
        for flowno_val in flowno_values:
            mask = col_str == flowno_val
            respondent_group = df[mask]
            
            if len(respondent_group) == 0:
                continue
            
            # Calculate null ratio for this group
            question_cols = [c for c in df.columns if c != 'phonenum']
            null_ratio = respondent_group[question_cols].isnull().mean().mean()
            
            # Calculate null ratio for ALL respondents
            overall_null_ratio = df[question_cols].isnull().mean().mean()
            
            # If this group has significantly higher null ratio (>30% higher than overall),
            # they're likely the "skip" group
            if null_ratio > overall_null_ratio + 0.3:
                # Also check: this group should have fewer respondents
                if len(respondent_group) < len(df) * 0.5:
                    screening_flows.append({
                        'col': col,
                        'skip_value': flowno_val,
                        'main_count': len(df) - len(respondent_group),
                        'skip_count': len(respondent_group),
                    })
    
    # Sort by column index (earlier columns are more likely to be screening)
    screening_flows.sort(key=lambda x: x['col'])
    return screening_flows


def filter_skip_logic(df: pd.DataFrame, skip_flow_no: str = "FlowNo_2=2") -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Handle IVR skip logic by splitting data into:
    - main_df: respondents who did NOT choose the skip option
    - skipped_df: respondents who were redirected
    
    Args:
        df: DataFrame with FlowNo values (before or after mapping)
        skip_flow_no: The FlowNo value that triggers the skip/redirect
    
    Returns:
        Tuple of (main_df, skipped_df)
    """
    skip_flow_no = skip_flow_no.strip()

    # Find the column that contains the skip flow value
    skip_col = None
    for col in df.columns:
        if col == 'phonenum':
            continue
        col_values = df[col].astype(str).str.strip()
        if (col_values == skip_flow_no).any():
            skip_col = col
            break

    if skip_col is None:
        return df, pd.DataFrame()

    # Split: rows where skip_col == skip_flow_no are the "skipped" respondents
    col_values = df[skip_col].astype(str).str.strip()
    skipped_mask = col_values == skip_flow_no
    main_df = df[~skipped_mask].copy()
    skipped_df = df[skipped_mask].copy()

    return main_df, skipped_df


def auto_filter_screening(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict]]:
    """
    Automatically detect and apply screening flow filtering.
    
    Tries multiple approaches:
    1. Auto-detect screening flows from data patterns
    2. Fall back to checking for common FlowNo_2=2 pattern
    
    Args:
        df: DataFrame with FlowNo values
    
    Returns:
        Tuple of (main_df, skipped_df, detected_flows)
    """
    # Try auto-detection
    detected = detect_screening_flows(df)
    
    if detected:
        # Use the first detected screening flow (most common case)
        screening = detected[0]
        main_df, skipped_df = filter_skip_logic(df, screening['skip_value'])
        return main_df, skipped_df, detected
    
    # Fallback: try common patterns
    common_patterns = [
        ("FlowNo_2=2", "Flow 2, Option 2 (Tidak)"),
        ("FlowNo_2=1", "Flow 2, Option 1 (Ya)"),
    ]
    
    for pattern, _ in common_patterns:
        main_df, skipped_df = filter_skip_logic(df, pattern)
        if not skipped_df.empty and len(skipped_df) < len(df):
            return main_df, skipped_df, [{'col': 'auto', 'skip_value': pattern, 
                                           'main_count': len(main_df), 'skip_count': len(skipped_df)}]
    
    # No screening detected
    return df, pd.DataFrame(), []


def clean_data(
    df: pd.DataFrame,
    completeness_threshold: float = 0.8,
    branch_groups: List[List[int]] = None,
    flow_to_question: Dict[int, str] = None,
) -> pd.DataFrame:
    """
    Clean IVR data with branch-aware completeness checking.

    Handles both simple and multi-layer skip logic by:
    1. Classifying columns as common vs branch-specific using branch_groups
    2. For branch groups, requiring respondents to answer at least ONE column
       in the group (not all)
    3. Only using common columns for the completeness threshold check
    4. Removing unmapped/extra columns before export
    5. Removing columns with 100% null (truly unused flows like FlowNo_1)

    Args:
        df: DataFrame to clean
        completeness_threshold: 0.0-1.0, minimum fraction of COMMON cols
                               that must be non-null (default 0.8)
        branch_groups: List of mutually exclusive flow groups from parser
        flow_to_question: Mapping from flow number to question text

    Returns:
        Cleaned DataFrame
    """
    df_clean = df.copy()

    # Step 1: Thoroughly replace null-like values with np.nan
    null_like = {'', ' ', '  ', 'nan', 'NaN', 'NAN', 'None', 'none', 'NONE',
                 'null', 'NULL', 'NaT', 'nat', 'N/A', 'n/a', 'NA', 'na',
                 'undefined', 'Nan', '<NA>'}

    for col in df_clean.columns:
        if col in ['phonenum']:
            continue
        df_clean[col] = df_clean[col].apply(
            lambda x: np.nan if (isinstance(x, str) and x.strip() in null_like) or
                      (isinstance(x, str) and x.strip() == '') else x
        )

    # Step 2: Identify question columns (all except phonenum and Mode)
    question_cols = [col for col in df_clean.columns if col not in ['phonenum', 'Mode']]

    # Step 3: Aggressive second pass - catch ANY remaining null-like values.
    flow_pattern = re.compile(r'^FlowNo_\d+=\d+$')
    for col in question_cols:
        def _to_nan(x, _pat=flow_pattern):
            if x is None or (isinstance(x, float) and np.isnan(x)):
                return np.nan
            if not isinstance(x, str):
                return x
            s = x.strip()
            if s == '' or s.lower() in {'nan', 'none', 'null', 'nat', 'n/a', 'na', '<na>'}:
                return np.nan
            if _pat.match(s):
                return np.nan
            return x
        df_clean[col] = df_clean[col].apply(_to_nan)

    # Step 3.5: Remove unmapped/extra columns.
    # Columns that are still numeric indices (int) were never renamed by the parser,
    # meaning they don't correspond to any question in the script.
    # Also remove columns that are 100% null (truly unused flows).
    cols_to_drop = []
    for col in df_clean.columns:
        if col in ['phonenum', 'Mode']:
            continue
        # Drop columns that are still numeric (unmapped)
        if isinstance(col, (int, float)):
            cols_to_drop.append(col)
            continue
        # Drop columns that are entirely NaN
        if df_clean[col].isnull().all():
            cols_to_drop.append(col)

    if cols_to_drop:
        df_clean = df_clean.drop(columns=cols_to_drop)

    # Recalculate question_cols after dropping
    question_cols = [col for col in df_clean.columns if col not in ['phonenum', 'Mode']]

    # Step 4: Classify columns as common vs branch-specific
    common_cols = question_cols
    branch_col_groups = {}

    if branch_groups and flow_to_question:
        common_cols_list, branch_specific_cols, branch_col_groups = _classify_columns(
            df_clean, branch_groups, flow_to_question
        )
        # common_cols_list may be empty if all columns are branch-specific
        # In that case, use all question_cols as common
        if common_cols_list:
            common_cols = common_cols_list

    # Step 5: Find the "last question" column for completion detection
    last_q_col = None
    for col in question_cols:
        col_lower = str(col).lower()
        if 'terakhir' in col_lower or 'last' in col_lower:
            last_q_col = col
            break

    # Step 6: Drop incomplete responses using branch-aware logic.
    #
    # Branch-aware completeness:
    # - For COMMON columns: apply the completeness threshold
    # - For BRANCH GROUPS: respondent is "complete" for a group if they have
    #   at least one non-null value in ANY column of the group
    #
    # IMPORTANT: Respondents who answered "Lain-lain" (Others) were redirected
    # to the end early (e.g., "Lain-lain" -> Call flow 24 = End). These are
    # VALID responses even though they skipped later questions.

    if last_q_col:
        # Primary strategy: keep respondents who answered the last question
        has_last_q = df_clean[last_q_col].notna()

        # For respondents who didn't answer the last question,
        # use branch-aware completeness
        if common_cols:
            # Count non-null common columns
            common_non_null = df_clean[common_cols].notna().sum(axis=1)

            # For each branch group, count as "complete" (1) if any column in the group
            # has a non-null value, else 0
            branch_completeness_score = pd.Series(0, index=df_clean.index)
            if branch_col_groups:
                for group_key, group_cols in branch_col_groups.items():
                    group_present = [c for c in group_cols if c in df_clean.columns]
                    if group_present:
                        group_has_any = df_clean[group_present].notna().any(axis=1).astype(int)
                        branch_completeness_score += group_has_any
                total_completeness = (common_non_null + branch_completeness_score) / (len(common_cols) + len(branch_col_groups))
            else:
                total_completeness = common_non_null / len(common_cols) if len(common_cols) > 0 else pd.Series(1.0, index=df_clean.index)

            keep_mask = has_last_q | (total_completeness >= completeness_threshold)
        else:
            keep_mask = has_last_q

        before_count = len(df_clean)
        df_clean = df_clean[keep_mask]
        dropped = before_count - len(df_clean)
        if dropped > 0:
            print(f"Dropped {dropped} incomplete responses")
    elif common_cols:
        # Fallback: no "last question" column - use branch-aware threshold
        common_non_null = df_clean[common_cols].notna().sum(axis=1)

        branch_completeness_score = pd.Series(0, index=df_clean.index)
        if branch_col_groups:
            for group_key, group_cols in branch_col_groups.items():
                group_present = [c for c in group_cols if c in df_clean.columns]
                if group_present:
                    group_has_any = df_clean[group_present].notna().any(axis=1).astype(int)
                    branch_completeness_score += group_has_any
            total_completeness = (common_non_null + branch_completeness_score) / (len(common_cols) + len(branch_col_groups))
        else:
            total_completeness = common_non_null / len(common_cols) if len(common_cols) > 0 else pd.Series(1.0, index=df_clean.index)

        df_clean = df_clean[total_completeness >= completeness_threshold]

    # Step 7: Remove duplicate phone numbers (keep first occurrence)
    if 'phonenum' in df_clean.columns:
        before_count = len(df_clean)
        df_clean = df_clean.drop_duplicates(subset='phonenum', keep='first')
        dupes_removed = before_count - len(df_clean)
        if dupes_removed > 0:
            print(f"Removed {dupes_removed} duplicate phone numbers")

    # Step 8: Add Mode column
    df_clean['Mode'] = 'IVR'

    return df_clean


def get_data_summary(df: pd.DataFrame) -> Dict:
    """
    Generate a summary of the DataFrame for the sanity check.

    Args:
        df: DataFrame to summarize

    Returns:
        Dict with summary information
    """
    summary = {
        'shape': df.shape,
        'columns': list(df.columns),
        'null_counts': df.isnull().sum().to_dict(),
        'value_counts': {}
    }

    for col in df.columns:
        if col != 'phonenum':
            vc = df[col].value_counts(dropna=False)
            summary['value_counts'][col] = vc.to_dict()

    return summary