"""Local input parsers."""

from resume_tailor.parsers.job_description import read_job_description, read_multiline_paste
from resume_tailor.parsers.markdown_cv import parse_markdown_cv

__all__ = ["parse_markdown_cv", "read_job_description", "read_multiline_paste"]
