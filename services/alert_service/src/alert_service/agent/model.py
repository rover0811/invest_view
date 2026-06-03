from __future__ import annotations


def build_gemini_model(config):
    """Build a Strands GeminiModel backed by Vertex AI.

    Imports of ``google.genai`` and ``strands`` are intentionally lazy (inside
    the function) so that importing this module never requires GCP credentials
    or the SDK to be installed at import time. The factory only runs when called.
    """
    from google import genai
    from strands.models.gemini import GeminiModel

    client = genai.Client(
        vertexai=True,
        project=config.gcp_project,
        location=config.gcp_location,
    )
    return GeminiModel(
        client=client,
        model_id=config.gemini_model_id,
        params={
            "temperature": config.gemini_temperature,
            "max_output_tokens": config.gemini_max_output_tokens,
        },
    )
