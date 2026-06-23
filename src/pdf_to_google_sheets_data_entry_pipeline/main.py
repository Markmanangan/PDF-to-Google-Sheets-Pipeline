#!/usr/bin/env python
import json
import os
import sys
from pathlib import Path
from crewai import LLM
from pdf_to_google_sheets_data_entry_pipeline.crew import PdfToGoogleSheetsDataEntryPipelineCrew


def _patch_groq_cache_breakpoint_compat() -> None:
    """Groq rejects CrewAI's internal cache_breakpoint message field."""
    if getattr(LLM, "_groq_cache_breakpoint_patched", False):
        return

    original = LLM._format_messages_for_provider

    def _format_messages_for_provider(self, messages):
        from crewai.llms.cache import CACHE_BREAKPOINT_KEY

        cleaned = [
            {key: value for key, value in message.items() if key != CACHE_BREAKPOINT_KEY}
            for message in messages
        ]
        return original(self, cleaned)

    LLM._format_messages_for_provider = _format_messages_for_provider
    LLM._groq_cache_breakpoint_patched = True


def _save_result(result, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = None
    if hasattr(result, "json_dict"):
        data = result.json_dict
    elif hasattr(result, "pydantic") and result.pydantic is not None:
        data = result.pydantic.dict()
    else:
        raw = getattr(result, "raw", None)
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"output": raw}
        else:
            data = raw

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(data, output_file, indent=2, ensure_ascii=False)

    return output_path


def run(pdf_file_path: str | None = None, output_filename: str = "processing_report.json"):
    """
    Run the crew and save the final report to a local JSON file.
    """
    _patch_groq_cache_breakpoint_compat()
    if pdf_file_path is None:
        pdf_file_path = r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'

    inputs = {
        "pdf_file_path": pdf_file_path,
    }

    crew = PdfToGoogleSheetsDataEntryPipelineCrew().crew()
    result = crew.kickoff(inputs=inputs)

    output_dir = Path(os.getcwd()) / "output"
    output_path = _save_result(result, output_dir / output_filename)
    print(f"Saved crew result to {output_path}")
    return result


def train():
    """
    Train the crew for a given number of iterations.
    """
    inputs = {
        'pdf_file_path': r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'
    }
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")

def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")

def test():
    """
    Test the crew execution and returns the results.
    """
    inputs = {
        'pdf_file_path': r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'
    }
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().test(n_iterations=int(sys.argv[1]), openai_model_name=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <command> [<args>]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "run":
        run()
    elif command == "train":
        train()
    elif command == "replay":
        replay()
    elif command == "test":
        test()
    elif command == "run_ui":
        from pdf_to_google_sheets_data_entry_pipeline.web import run_app

        run_app()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
