"""
Utility to create a random sample of rows from the main sheet of
``data/chatbot_question.xlsx`` (or another workbook) and write the sample to a
new sheet in the same file.

Usage:
    python random_question_sampler.py --workbook data/chatbot_question.xlsx \
        --sample-size 20 --sheet-name "Random Sample"

The tool reads the first sheet in the workbook (considered the main sheet),
randomly selects the requested number of rows, and writes them into a new sheet
while preserving the existing content of the workbook. If the requested sample
size exceeds the available rows, all rows are written.
"""

from pathlib import Path
import argparse

import pandas as pd


def sample_main_sheet(workbook_path: Path, sample_size: int, sample_sheet: str) -> pd.DataFrame:
    """Sample rows from the main sheet and write them to a new sheet.

    Args:
        workbook_path: Path to the Excel workbook to read and update.
        sample_size: Desired number of rows to sample.
        sample_sheet: Name of the sheet where the sample will be stored.

    Returns:
        DataFrame containing the sampled rows.
    """

    if sample_size <= 0:
        raise ValueError("sample_size must be positive")

    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    excel_file = pd.ExcelFile(workbook_path)
    main_sheet = excel_file.sheet_names[0]
    main_df = pd.read_excel(excel_file, sheet_name="Main")
    print("Hello main_df ", main_df)

    if main_df.empty:
        raise ValueError("Main sheet is empty; cannot sample rows.")

    rows_to_sample = min(sample_size, len(main_df))
    print("Hello: ", rows_to_sample)
    sample_df = main_df.sample(n=rows_to_sample)
    print("Hello sample_df: ", sample_df )

    with pd.ExcelWriter(
        workbook_path, engine="openpyxl", mode="a", if_sheet_exists="replace"
    ) as writer:
        sample_df.to_excel(writer, sheet_name=sample_sheet, index=False)

    return sample_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sample rows from the main sheet of an Excel workbook and write them "
            "to a new sheet."
        )
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("data/chatbot_question.xlsx"),
        help="Path to the Excel workbook to sample from.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=60,
        help="Number of rows to randomly sample from the main sheet.",
    )
    parser.add_argument(
        "--sheet-name",
        type=str,
        default="Random Sample 2",
        help="Name of the sheet to write the sampled rows to.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sample_df = sample_main_sheet(args.workbook, args.sample_size, args.sheet_name)
    print(
        f"Sampled {len(sample_df)} rows from '{args.workbook}' into sheet "
        f"'{args.sheet_name}'."
    )


if __name__ == "__main__":
    main()
