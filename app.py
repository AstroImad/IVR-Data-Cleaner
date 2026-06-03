"""
IVR Data Cleaner - Streamlit App
Cleans Interactive Voice Response (IVR) survey data by:
1. Loading CSV data from Google Drive
2. Parsing IVR script documents to extract question/answer mappings
3. Renaming columns and mapping flow values to readable text
4. Cleaning and exporting the data to Excel
"""

import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from parsers import parse_ivr_script
from cleaning import (
    load_all_csvs_from_folder,
    load_all_csvs_from_bytes,
    load_csvs_from_zip,
    load_csv_from_gdrive_links,
    detect_flow_columns,
    apply_column_renames,
    apply_flow_value_mapping,
    filter_skip_logic,
    clean_data,
    get_data_summary,
)

# ─── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="IVR Data Cleaner",
    page_icon="📞",
    layout="wide",
)

st.title("📞 IVR Data Cleaner")
st.markdown("Clean and process Interactive Voice Response (IVR) survey data.")

# ─── Session State Initialization ──────────────────────────────────────────────
if 'step' not in st.session_state:
    st.session_state.step = 1
if 'raw_df' not in st.session_state:
    st.session_state.raw_df = None
if 'num_files' not in st.session_state:
    st.session_state.num_files = 0
if 'flow_to_question' not in st.session_state:
    st.session_state.flow_to_question = {}
if 'flow_value_mapping' not in st.session_state:
    st.session_state.flow_value_mapping = {}
if 'flow_to_cols' not in st.session_state:
    st.session_state.flow_to_cols = {}
if 'rename_map' not in st.session_state:
    st.session_state.rename_map = {}
if 'mapped_df' not in st.session_state:
    st.session_state.mapped_df = None
if 'cleaned_df' not in st.session_state:
    st.session_state.cleaned_df = None

# ─── Helper Functions ──────────────────────────────────────────────────────────

def reset_from_step(step: int):
    """Reset all session state from the given step onwards."""
    if step <= 1:
        st.session_state.raw_df = None
        st.session_state.num_files = 0
    if step <= 2:
        st.session_state.flow_to_question = {}
        st.session_state.flow_value_mapping = {}
    if step <= 3:
        st.session_state.flow_to_cols = {}
        st.session_state.rename_map = {}
        st.session_state.mapped_df = None
    if step <= 4:
        st.session_state.cleaned_df = None
    st.session_state.step = step


def to_excel(df: pd.DataFrame) -> bytes:
    """Convert DataFrame to Excel bytes for download."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='IVR Cleaned')
    return output.getvalue()


# ─── Sidebar: Progress ────────────────────────────────────────────────────────
st.sidebar.header("📋 Progress")
steps = {
    1: "Load Data",
    2: "Upload IVR Script",
    3: "Rename & Map Columns",
    4: "Sanity Check & Export",
}
for step_num, step_name in steps.items():
    if step_num < st.session_state.step:
        st.sidebar.markdown(f"✅ **Step {step_num}:** {step_name}")
    elif step_num == st.session_state.step:
        st.sidebar.markdown(f"🔵 **Step {step_num}:** {step_name}")
    else:
        st.sidebar.markdown(f"⬜ **Step {step_num}:** {step_name}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state.step == 1:
    st.header("Step 1: Load Data")
    
    st.markdown("""
    Upload or link your IVR CSV files to get started.
    """)
    
    # Input method selection
    input_method = st.radio(
        "Choose input method:",
        ["Upload CSV Files", "Google Drive File Links"],
        horizontal=True,
    )
    
    if input_method == "Upload CSV Files":
        upload_type = st.radio(
            "File format:",
            ["ZIP file (recommended)", "Individual CSV files"],
            horizontal=True,
        )

        if upload_type == "ZIP file (recommended)":
            uploaded_zip = st.file_uploader(
                "Upload ZIP file containing CSV files",
                type=['zip'],
                accept_multiple_files=False,
                help="Upload a ZIP file containing one or more IVR CSV files."
            )

            if uploaded_zip and st.button("📂 Load from ZIP", type="primary"):
                with st.spinner("Extracting and loading CSV files from ZIP..."):
                    try:
                        zip_bytes = uploaded_zip.read()
                        combined_df, num_files, file_names = load_csvs_from_zip(zip_bytes)
                        st.session_state.raw_df = combined_df
                        st.session_state.num_files = num_files
                        st.success(f"Successfully loaded {num_files} CSV file(s) with {len(combined_df)} total rows.")
                        with st.expander(f"📂 Files loaded from ZIP ({num_files})"):
                            for name in file_names:
                                st.write(f"- {name}")
                    except Exception as e:
                        st.error(f"Error loading data: {str(e)}")
        else:
            uploaded_csvs = st.file_uploader(
                "Upload CSV Files",
                type=['csv'],
                accept_multiple_files=True,
                help="Upload one or more IVR CSV files."
            )

            if uploaded_csvs and st.button("📂 Load Uploaded Data", type="primary"):
                with st.spinner("Loading CSV files..."):
                    try:
                        combined_df = load_all_csvs_from_bytes(uploaded_csvs)
                        st.session_state.raw_df = combined_df
                        st.session_state.num_files = len(uploaded_csvs)
                        st.success(f"Successfully loaded {len(uploaded_csvs)} CSV file(s) with {len(combined_df)} total rows.")
                    except Exception as e:
                        st.error(f"Error loading data: {str(e)}")
    
    else:
        st.markdown("""
        Paste Google Drive file links below (one per line). 
        Each file must be shared as **"Anyone with the link"**.
        
        Example:
        ```
        https://drive.google.com/file/d/FILE_ID_1/view
        https://drive.google.com/file/d/FILE_ID_2/view
        ```
        """)
        
        gdrive_links = st.text_area(
            "Google Drive File Links",
            placeholder="https://drive.google.com/file/d/xxxxx/view\nhttps://drive.google.com/file/d/yyyyy/view",
            help="Paste Google Drive file links, one per line.",
            height=150,
        )
        
        if st.button("📂 Load Data from Google Drive", type="primary", disabled=not gdrive_links.strip()):
            with st.spinner("Loading CSV files from Google Drive..."):
                try:
                    combined_df, num_files = load_csv_from_gdrive_links(gdrive_links)
                    st.session_state.raw_df = combined_df
                    st.session_state.num_files = num_files
                    st.success(f"Successfully loaded {num_files} CSV file(s) with {len(combined_df)} total rows.")
                except Exception as e:
                    st.error(f"Error loading data: {str(e)}")
                    st.info("💡 Make sure each file is shared as 'Anyone with the link can view'.")
    
    # Show raw data preview if loaded
    if st.session_state.raw_df is not None:
        st.divider()
        st.subheader("📊 Raw Data Preview")
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Rows", f"{st.session_state.raw_df.shape[0]:,}")
        col2.metric("Total Columns", f"{st.session_state.raw_df.shape[1]}")
        col3.metric("Files Loaded", st.session_state.num_files)
        
        st.dataframe(st.session_state.raw_df.head(20), use_container_width=True)
        
        st.subheader("📋 Columns")
        st.write(f"**Columns:** {list(st.session_state.raw_df.columns)}")
        
        if st.button("➡️ Continue to Step 2: Upload IVR Script", type="primary"):
            st.session_state.step = 2
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: UPLOAD IVR SCRIPT
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == 2:
    st.header("Step 2: Upload IVR Script")
    
    st.markdown("""
    Upload the IVR call script document (PDF or DOCX). The script contains:
    - **Questions** associated with each call flow
    - **Answer choices** (Tekan N untuk ...)
    """)
    
    uploaded_script = st.file_uploader(
        "Upload IVR Script",
        type=['pdf', 'docx', 'doc'],
        help="Upload the IVR script document (PDF or DOCX)."
    )
    
    if uploaded_script and st.button("🔍 Parse Script", type="primary"):
        with st.spinner("Parsing IVR script..."):
            try:
                file_bytes = uploaded_script.read()
                flow_to_question, flow_value_mapping = parse_ivr_script(
                    file_bytes, uploaded_script.name
                )
                
                if not flow_to_question:
                    st.warning("No questions found in the script. Please check the document format.")
                else:
                    st.session_state.flow_to_question = flow_to_question
                    st.session_state.flow_value_mapping = flow_value_mapping
                    st.success(f"Parsed {len(flow_to_question)} questions and {len(flow_value_mapping)} answer mappings.")
            except Exception as e:
                st.error(f"Error parsing script: {str(e)}")
    
    # Show parsed results if available
    if st.session_state.flow_to_question:
        st.divider()
        st.subheader("📝 Parsed Questions")
        
        for flow_num, question in sorted(st.session_state.flow_to_question.items()):
            with st.expander(f"Call Flow {flow_num}: {question[:80]}..."):
                st.markdown(f"**Full Question:** {question}")
                # Show answers for this flow
                flow_answers = {
                    k: v for k, v in st.session_state.flow_value_mapping.items()
                    if k.startswith(f"FlowNo_{flow_num}=")
                }
                if flow_answers:
                    st.markdown("**Answer Choices:**")
                    for key, answer in sorted(flow_answers.items()):
                        st.markdown(f"- `{key}` → {answer}")
        
        st.divider()
        st.subheader("✏️ Edit Mappings (Optional)")
        st.markdown("You can edit the parsed questions and answer mappings below before proceeding.")
        
        # Editable questions
        with st.expander("Edit Questions"):
            edited_questions = {}
            for flow_num in sorted(st.session_state.flow_to_question.keys()):
                current_q = st.session_state.flow_to_question[flow_num]
                new_q = st.text_input(
                    f"Call Flow {flow_num}",
                    value=current_q,
                    key=f"q_edit_{flow_num}"
                )
                edited_questions[flow_num] = new_q
            
            if st.button("Save Question Edits"):
                st.session_state.flow_to_question = edited_questions
                st.success("Questions updated!")
                st.rerun()
        
        # Editable answer mappings (sorted by FlowNo_X=Y in ascending order)
        with st.expander("Edit Answer Mappings"):
            st.markdown("Format: `FlowNo_X=Y` → answer text (one per line)")
            def _sort_flowno_key(item):
                """Sort FlowNo_X=Y by X then Y numerically."""
                key = item[0]
                match = re.match(r'FlowNo_(\d+)=(\d+)', key)
                if match:
                    return (int(match.group(1)), int(match.group(2)))
                return (999, 999)
            mapping_text = "\n".join([
                f"{k} = {v}" for k, v in sorted(st.session_state.flow_value_mapping.items(), key=_sort_flowno_key)
            ])
            edited_mapping_text = st.text_area(
                "Answer Mappings",
                value=mapping_text,
                height=300,
                key="mapping_edit"
            )
            
            if st.button("Save Mapping Edits"):
                new_mapping = {}
                for line in edited_mapping_text.strip().split('\n'):
                    line = line.strip()
                    if '=' in line:
                        parts = line.split('=', 1)
                        key = parts[0].strip()
                        val = parts[1].strip()
                        if key and val:
                            new_mapping[key] = val
                st.session_state.flow_value_mapping = new_mapping
                st.success("Answer mappings updated!")
                st.rerun()
        
        st.divider()
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⬅️ Back to Step 1"):
                reset_from_step(1)
                st.session_state.step = 1
                st.rerun()
        with col2:
            if st.button("➡️ Continue to Step 3: Rename & Map", type="primary"):
                st.session_state.step = 3
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: RENAME & MAP COLUMNS
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == 3:
    st.header("Step 3: Rename Columns & Map Flow Values")
    
    df = st.session_state.raw_df.copy()
    
    # Detect flow columns
    if not st.session_state.flow_to_cols:
        st.session_state.flow_to_cols = detect_flow_columns(df)
    
    flow_to_cols = st.session_state.flow_to_cols
    flow_to_question = st.session_state.flow_to_question
    flow_value_mapping = st.session_state.flow_value_mapping
    
    # Show detected mappings
    st.subheader("🔍 Detected Column → Flow Mappings")
    
    if not flow_to_cols:
        st.warning("No FlowNo patterns detected in the data columns. Please check your data.")
    else:
        mapping_data = []
        for flow_num in sorted(flow_to_cols.keys()):
            cols = flow_to_cols[flow_num]
            question = flow_to_question.get(flow_num, "❓ No question found")
            for col_idx in cols:
                mapping_data.append({
                    "Data Column": col_idx,
                    "Flow Number": flow_num,
                    "Question": question[:100] + "..." if len(question) > 100 else question,
                })
        
        mapping_df = pd.DataFrame(mapping_data)
        st.dataframe(mapping_df, use_container_width=True)
        
        # Check for unmatched flows
        unmatched_flows = [
            fn for fn in flow_to_cols.keys()
            if fn not in flow_to_question
        ]
        if unmatched_flows:
            st.warning(f"⚠️ Flow number(s) {unmatched_flows} found in data but not in the script. These columns will keep their numeric names.")
        
        unmatched_questions = [
            fn for fn in flow_to_question.keys()
            if fn not in flow_to_cols
        ]
        if unmatched_questions:
            st.warning(f"⚠️ Question(s) for flow(s) {unmatched_questions} found in script but not detected in data.")
    
    st.divider()
    
    # Apply transformations
    if st.button("🔄 Apply Column Renaming & Flow Mapping", type="primary"):
        with st.spinner("Applying transformations..."):
            # Step 3a: Rename columns
            df_renamed, rename_map = apply_column_renames(
                df, flow_to_question, flow_to_cols
            )
            st.session_state.rename_map = rename_map
            
            # Step 3b: Map flow values
            df_mapped = apply_flow_value_mapping(df_renamed, flow_value_mapping)
            
            st.session_state.mapped_df = df_mapped
            st.success("Column renaming and flow mapping applied!")
    
    # Show result if available
    if st.session_state.mapped_df is not None:
        st.divider()
        st.subheader("📊 Data After Renaming & Mapping")

        col1, col2 = st.columns(2)
        col1.metric("Rows", f"{st.session_state.mapped_df.shape[0]:,}")
        col2.metric("Columns", st.session_state.mapped_df.shape[1])

        st.dataframe(st.session_state.mapped_df.head(20), use_container_width=True)

        st.subheader("📋 Renamed Columns")
        for i, col in enumerate(st.session_state.mapped_df.columns):
            st.write(f"**{i+1}.** {col}")

        # ── Check for unmapped FlowNo values ──────────────────────────────
        st.divider()
        st.subheader("🔍 Unmapped Values Check")

        unmapped_values = set()
        for col in st.session_state.mapped_df.columns:
            if col in ['phonenum', 'Mode']:
                continue
            for val in st.session_state.mapped_df[col].dropna().unique():
                if isinstance(val, str) and re.match(r'FlowNo_\d+=\d+', val):
                    unmapped_values.add(val)

        if unmapped_values:
            st.warning(f"⚠️ Found {len(unmapped_values)} unmapped FlowNo values. You can fix them below:")

            # Inline editing for unmapped values
            edited_unmapped = {}
            for val in sorted(unmapped_values):
                col1_edit, col2_edit = st.columns([1, 2])
                with col1_edit:
                    st.code(val)
                with col2_edit:
                    new_val = st.text_input(
                        f"Map `{val}` to:",
                        value="",
                        placeholder="Enter answer text or leave empty to skip",
                        key=f"unmapped_{val}",
                    )
                    if new_val.strip():
                        edited_unmapped[val] = new_val.strip()

            if edited_unmapped and st.button("🔄 Apply Unmapped Mappings", type="primary"):
                # Apply the new mappings to the DataFrame
                df_fixed = st.session_state.mapped_df.replace(edited_unmapped)
                st.session_state.mapped_df = df_fixed
                # Also add to flow_value_mapping for future reference
                st.session_state.flow_value_mapping.update(edited_unmapped)
                st.success(f"Applied {len(edited_unmapped)} mapping(s)!")
                st.rerun()
        else:
            st.success("✅ All FlowNo values have been mapped to answer text!")

        # ── Skip Logic ────────────────────────────────────────────────────
        st.divider()
        st.subheader("🔀 Skip Logic Handling")

        # Detect if there's a branching flow (FlowNo_2 is typically the filter)
        main_df, skipped_df = filter_skip_logic(st.session_state.mapped_df, "FlowNo_2=2")

        if not skipped_df.empty:
            st.info(
                f"Detected skip logic: **{len(skipped_df)}** respondents chose the skip option "
                f"(FlowNo_2=2) and were redirected to different questions. "
                f"**{len(main_df)}** respondents continued with the main survey."
            )

            keep_main = st.checkbox(
                "Keep only main survey respondents (FlowNo_2=1)",
                value=True,
                help="Uncheck to keep all respondents including those who were redirected."
            )

            if keep_main:
                # Re-apply after any unmapped value fixes
                main_df, skipped_df = filter_skip_logic(st.session_state.mapped_df, "FlowNo_2=2")
                st.session_state.mapped_df = main_df
                st.success(f"Keeping {len(main_df)} main survey respondents.")
            else:
                st.info("Keeping all respondents.")
        else:
            st.info("No skip logic detected (no FlowNo_2=2 values found).")

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⬅️ Back to Step 2"):
                reset_from_step(2)
                st.session_state.step = 2
                st.rerun()
        with col2:
            if st.button("➡️ Continue to Step 4: Sanity Check", type="primary"):
                st.session_state.step = 4
                st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: SANITY CHECK & EXPORT
# ═══════════════════════════════════════════════════════════════════════════════
elif st.session_state.step == 4:
    st.header("Step 4: Sanity Check & Export")
    
    if st.session_state.mapped_df is None:
        st.error("No mapped data available. Please go back to Step 3.")
        if st.button("⬅️ Back to Step 3"):
            st.session_state.step = 3
            st.rerun()
    else:
        # Apply cleaning
        if st.session_state.cleaned_df is None:
            with st.spinner("Cleaning data..."):
                st.session_state.cleaned_df = clean_data(st.session_state.mapped_df)
        
        df = st.session_state.cleaned_df
        
        # ─── Summary Stats ─────────────────────────────────────────────────
        st.subheader("📊 Data Summary")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Rows", f"{df.shape[0]:,}")
        col2.metric("Total Columns", df.shape[1])
        col3.metric("Respondents", f"{df['phonenum'].nunique():,}")
        col4.metric("Null Cells", f"{df.isnull().sum().sum():,}")
        
        # ─── Data Preview ──────────────────────────────────────────────────
        st.subheader("👀 Data Preview")
        st.dataframe(df.head(50), use_container_width=True)
        
        # ─── Column Info ───────────────────────────────────────────────────
        st.subheader("📋 Column Details")
        
        col_info = []
        for col in df.columns:
            col_info.append({
                "Column": col[:60] + "..." if len(str(col)) > 60 else str(col),
                "Non-Null": df[col].notna().sum(),
                "Null": df[col].isna().sum(),
                "Null %": f"{df[col].isna().mean()*100:.1f}%",
                "Unique": df[col].nunique(),
            })
        
        col_info_df = pd.DataFrame(col_info)
        st.dataframe(col_info_df, use_container_width=True, hide_index=True)
        
        # ─── Value Counts ──────────────────────────────────────────────────
        st.subheader("📈 Value Counts per Column")
        
        for col in df.columns:
            if col in ['phonenum']:
                continue
            with st.expander(f"📊 {col[:80]}"):
                vc = df[col].value_counts(dropna=False).reset_index()
                vc.columns = ['Value', 'Count']
                vc['Percentage'] = (vc['Count'] / len(df) * 100).round(1).astype(str) + '%'
                st.dataframe(vc, use_container_width=True, hide_index=True)
        
        # ─── Data Issues ───────────────────────────────────────────────────
        st.subheader("⚠️ Potential Issues")
        
        issues_found = False
        
        # Check for remaining FlowNo values
        for col in df.columns:
            if col in ['phonenum', 'Mode']:
                continue
            for val in df[col].dropna().unique():
                if isinstance(val, str) and re.match(r'FlowNo_\d+=\d+', val):
                    if not issues_found:
                        st.warning("Found unmapped FlowNo values:")
                        issues_found = True
                    st.write(f"  Column **{col[:50]}**: `{val}`")
        
        # Check for high null percentage columns
        for col in df.columns:
            null_pct = df[col].isna().mean()
            if null_pct > 0.5:
                if not issues_found:
                    st.warning("Columns with high null percentage:")
                    issues_found = True
                st.write(f"  **{col[:60]}**: {null_pct*100:.1f}% null")
        
        if not issues_found:
            st.success("✅ No major issues detected!")
        
        # ─── Export ────────────────────────────────────────────────────────
        st.divider()
        st.subheader("📥 Export Data")
        
        excel_bytes = to_excel(df)
        
        st.download_button(
            label="📥 Download as Excel",
            data=excel_bytes,
            file_name="ivr_cleaned.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⬅️ Back to Step 3"):
                reset_from_step(3)
                st.session_state.step = 3
                st.rerun()
        with col2:
            if st.button("🔄 Start Over"):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

# ─── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption("IVR Data Cleaner • Built with Streamlit")