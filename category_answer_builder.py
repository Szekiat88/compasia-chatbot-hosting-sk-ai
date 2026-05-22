"""Create an Excel worksheet that maps categories to knowledge-base answers.

This utility reads the first column of the questions workbook (categories) and
looks up matching answers from the first column of the knowledge base
(`keyword`). The resulting DataFrame is written back to the questions workbook
on a dedicated sheet.
"""
from pathlib import Path
from typing import Tuple

import pandas as pd
from dotenv import load_dotenv

from excel_utils import load_knowledge_base, save_dataframe_to_excel

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

QUESTIONS_EXCEL_PATH = BASE_DIR / "data" / "chatbot_question.xlsx"
QUESTIONS_SHEET_NAME = "Random Sample"
OUTPUT_SHEET_NAME = "Category Answers"

KNOWLEDGE_EXCEL_PATH = BASE_DIR / "data" / "Samples.xlsx"
KNOWLEDGE_SHEET_NAME = "Main DB"


def _load_categories(path: Path, sheet_name: str) -> Tuple[pd.DataFrame, str]:
    """Load the category sheet and return the DataFrame plus category column name."""
    questions_df = pd.read_excel(path, sheet_name=sheet_name)

    if questions_df.empty:
        raise ValueError(f"The sheet '{sheet_name}' in {path} is empty.")

    category_column = questions_df.columns[0]
    return questions_df, category_column


def _normalize(series: pd.Series) -> pd.Series:
    """Normalize text for consistent matching."""
    return series.astype(str).str.strip().str.lower()


def build_category_answers_sheet(
    *,
    questions_path: Path = QUESTIONS_EXCEL_PATH,
    questions_sheet: str = QUESTIONS_SHEET_NAME,
    knowledge_path: Path = KNOWLEDGE_EXCEL_PATH,
    knowledge_sheet: str = KNOWLEDGE_SHEET_NAME,
    output_sheet: str = OUTPUT_SHEET_NAME,
) -> pd.DataFrame:
    """Match categories to knowledge-base answers and write them to Excel."""
    knowledge_df = load_knowledge_base(knowledge_path, knowledge_sheet)
    knowledge_df["keyword_normalized"] = _normalize(knowledge_df["keyword"])

    knowledge_lookup = dict(
        zip(knowledge_df["keyword_normalized"], knowledge_df["answer"])
    )

    categories_df, category_column = _load_categories(questions_path, questions_sheet)

    answers = []
    for category in categories_df[category_column]:
        if pd.isna(category):
            answers.append("")
            continue

        normalized_category = str(category).strip().lower()
        answers.append(knowledge_lookup.get(normalized_category, ""))

    categories_df["Answer"] = answers

    save_dataframe_to_excel(
        categories_df,
        questions_path,
        output_sheet or questions_sheet,
    )

    return categories_df


if __name__ == "__main__":
    build_category_answers_sheet()
