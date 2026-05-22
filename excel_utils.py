"""
Utility helpers for loading and saving Excel workbooks.
"""
from __future__ import annotations

from pathlib import Path
import re
import pandas as pd


_ILLEGAL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def load_knowledge_base(path, sheet_name):
    """Load the knowledge base Excel sheet and return a normalized DataFrame."""

    excel_path = Path(path)

    if not excel_path.exists():
        raise FileNotFoundError(
            f"Knowledge base workbook not found at {excel_path}."
        )

    knowledge_df = pd.read_excel(excel_path, sheet_name=sheet_name)

    # Drop placeholder "Unnamed" columns or columns that are entirely empty to avoid
    # length mismatch errors when renaming.
    knowledge_df = knowledge_df.loc[:, ~knowledge_df.columns.str.contains("^Unnamed")]

    # Normalize column names to simplify downstream lookups.
    knowledge_df.columns = [col.strip().lower() for col in knowledge_df.columns]

    if "keyword" not in knowledge_df.columns:
        raise ValueError("The knowledge base must contain a 'keyword' column.")

    if "answer" not in knowledge_df.columns:
        raise ValueError("The knowledge base must contain an 'answer' column.")

    return knowledge_df



def load_questions_excel(path, sheet_name):
    """Load a questions worksheet and validate it contains a Question column."""
    excel_path = Path(path)

    if not excel_path.exists():
        raise FileNotFoundError(
            f"Questions workbook not found at {excel_path}."
        )

    questions_df = pd.read_excel(
        excel_path,
        sheet_name=sheet_name,
    )

    question_column = next(
        (col for col in questions_df.columns if col.strip().lower() == "question"),
        None,
    )

    if not question_column:
        raise ValueError(
            f"Expected a 'Question' column in the {sheet_name} sheet."
        )

    return questions_df, question_column


def excel_column_to_index(column_ref):
    """Convert an Excel-style column reference (e.g., "G") to a zero-based index."""

    if isinstance(column_ref, int):
        if column_ref < 1:
            raise ValueError("Column indices must be 1-based when provided as integers.")
        return column_ref - 1

    if not isinstance(column_ref, str):
        raise TypeError("Column references must be a string letter or 1-based integer.")

    column_ref = column_ref.strip().upper()
    if not column_ref.isalpha():
        raise ValueError("Column letters must contain only alphabetic characters.")

    index = 0
    for char in column_ref:
        index = index * 26 + (ord(char) - ord("A") + 1)

    return index - 1


def reorder_columns_by_excel_position(df, column_positions=None):
    """Return a copy of ``df`` with specific columns moved to Excel positions.

    ``column_positions`` should map column names to either Excel letters (e.g., ``"G"``)
    or 1-based indices (e.g., ``7``). Columns not listed keep their original relative
    order.
    """

    if not column_positions:
        return df

    target_indices = {}
    for column_name, position in column_positions.items():
        if column_name not in df.columns:
            raise KeyError(f"Column '{column_name}' not found in DataFrame.")
        target_indices[column_name] = excel_column_to_index(position)

    remaining_columns = [c for c in df.columns if c not in target_indices]
    max_target_index = max(target_indices.values(), default=-1)
    output_length = max(len(remaining_columns) + len(target_indices), max_target_index + 1)
    ordered_columns = [None] * output_length

    for column_name, target_index in sorted(target_indices.items(), key=lambda item: item[1]):
        if target_index >= len(ordered_columns):
            ordered_columns.extend([None] * (target_index + 1 - len(ordered_columns)))
        if ordered_columns[target_index] is not None:
            raise ValueError(
                f"Multiple columns assigned to Excel position {target_index + 1}."
            )
        ordered_columns[target_index] = column_name

    remaining_iter = iter(remaining_columns)
    for i, column_name in enumerate(ordered_columns):
        if column_name is None:
            try:
                ordered_columns[i] = next(remaining_iter)
            except StopIteration:
                break

    ordered_columns.extend(list(remaining_iter))

    return df[ordered_columns]


def _strip_illegal_characters(value):
    """Remove characters that Excel cannot store in worksheet cells."""

    if isinstance(value, str):
        return _ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def sanitize_dataframe_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with illegal Excel characters removed.

    The sanitizer cleans both cell values and column headers to prevent OpenPyXL
    from raising ``IllegalCharacterError`` when writing workbooks.
    """

    cleaned_df = df.copy()
    cleaned_df.columns = [
        _strip_illegal_characters(str(column_name)) for column_name in cleaned_df.columns
    ]
    cleaned_df = cleaned_df.map(_strip_illegal_characters) if hasattr(cleaned_df, "map") else cleaned_df.applymap(_strip_illegal_characters)
    return cleaned_df


def append_rows_to_sheet(df: pd.DataFrame, path, sheet_name: str) -> pd.DataFrame:
    """Append rows to a worksheet, preserving existing content.

    The function reads the target ``sheet_name`` if it exists, appends the
    supplied rows, and writes the combined data back while keeping the rest of
    the workbook intact.

    Args:
        df: DataFrame containing the rows to append.
        path: Path to the Excel workbook.
        sheet_name: Name of the worksheet to append to.

    Returns:
        The combined DataFrame that was written to the sheet.
    """

    excel_path = Path(path)

    existing_df = pd.DataFrame()
    if excel_path.exists():
        try:
            existing_df = pd.read_excel(excel_path, sheet_name=sheet_name)
        except ValueError:
            existing_df = pd.DataFrame()

    combined_df = pd.concat([existing_df, df], ignore_index=True)
    combined_df = sanitize_dataframe_for_excel(combined_df)

    with pd.ExcelWriter(
        excel_path,
        engine="openpyxl",
        mode="a" if excel_path.exists() else "w",
        if_sheet_exists="replace",
    ) as writer:
        combined_df.to_excel(writer, sheet_name=sheet_name, index=False)

    return combined_df


def save_dataframe_to_excel(df, path, sheet_name, column_positions=None):
    """Persist a DataFrame back to a specific worksheet.

    Parameters
    ----------
    column_positions : dict[str, str | int] | None
        Optional mapping of column names to Excel letters (e.g., ``"G"``) or 1-based
        indices indicating where the columns should be placed in the saved sheet.
        Unspecified columns retain their relative order.
    """
    excel_path = Path(path)
    prepared_df = reorder_columns_by_excel_position(df, column_positions)
    prepared_df = sanitize_dataframe_for_excel(prepared_df)

    with pd.ExcelWriter(
        excel_path,
        mode="a",
        engine="openpyxl",
        if_sheet_exists="replace",
    ) as writer:
        prepared_df.to_excel(
            writer,
            sheet_name=sheet_name,
            index=False,
        )
