"""
Sub-Agent A: Intake Agent

Authors: Syed Ali Turab, Fergie Feng & Diana Liu | Team: Broadview
Date:   March 1, 2026
Code updated: Syed Ali Turab, March 4, 2026 — LLM-powered intake with diagnosis guardrails.

Collects pet profile, chief complaint, and symptom details through
adaptive, multi-turn follow-up questions tailored to species and symptom area.

This agent is the first in the pipeline and handles the conversational
intake flow. It:
  1. Parses the owner's free-text message for pet profile fields
     (species, breed, age, weight, name)
  2. Identifies the symptom area (GI, respiratory, derm, injury, urinary, etc.)
  3. Asks adaptive follow-up questions specific to the symptom area
  4. Builds a structured output that downstream agents can consume

The intake process is multi-turn: the agent may need 2-5 messages to
collect all required information. The Orchestrator manages the turn loop.

Input:  Owner's free-text message + current session state
Output: Structured pet_profile + symptom_details + follow-up questions
"""

import logging
import os
import json
import openai

logger = logging.getLogger('petcare.agents.intake')

# ---------------------------------------------------------------------------
# LLM intake: os for env (OPENAI_API_KEY), json for parsing LLM response,
# openai for chat completions. Updated March 4, 2026 — Syed Ali Turab.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Field Definitions
# ---------------------------------------------------------------------------

# These fields MUST be collected before we can proceed to triage.
# If missing, the Confidence Gate will flag them.
REQUIRED_FIELDS = ['species', 'chief_complaint']

# These fields improve triage quality but aren't strictly required.
# Missing optional fields lower confidence but don't block the flow.
OPTIONAL_FIELDS = [
    'pet_name', 'breed', 'age', 'weight',
    'timeline', 'eating_drinking', 'energy_level'
]

# ---------------------------------------------------------------------------
# Symptom-Area-Specific Follow-Up Questions
# ---------------------------------------------------------------------------
# When the intake agent identifies a symptom area, it selects the
# appropriate set of follow-up questions. These are designed to collect
# the minimum information needed for accurate triage.
#
# Each area's questions target the key differentiating factors that
# determine urgency (e.g., blood in vomit → higher urgency for GI).

SYMPTOM_AREA_FOLLOWUPS = {
    'gastrointestinal': [
        'How many times has your pet vomited in the last 24 hours?',
        'Is there any diarrhea?',
        'Is there any blood in the vomit or stool?',
        'Could your pet have eaten something unusual (garbage, toys, plants)?'
    ],
    'respiratory': [
        'Is your pet having difficulty breathing or breathing rapidly?',
        'Is there coughing? If so, is it dry or productive?',
        'Any nasal discharge?',
        'Is the cough worse at night or after exercise?'
    ],
    'dermatological': [
        'Where on the body is the skin issue?',
        'Is there itching, redness, or hair loss?',
        'How long has this been going on?',
        'Any new foods, products, or environmental changes recently?'
    ],
    'injury': [
        'Where is the injury located?',
        'Is your pet able to walk/move normally?',
        'Is there swelling or bleeding?',
        'Do you know what caused the injury?'
    ],
    'urinary': [
        'Is your pet straining to urinate?',
        'Is there blood in the urine?',
        'How frequently is your pet trying to urinate?',
        'Is your pet able to produce any urine?'
    ],
    'neurological': [
        'Has your pet had any seizures or tremors?',
        'Is your pet disoriented or walking in circles?',
        'Any head tilting or loss of balance?',
        'How long have these symptoms been occurring?'
    ],
    'behavioral': [
        'What specific behavior changes have you noticed?',
        'When did the changes start?',
        'Has anything in the environment changed recently?',
        'Is your pet eating and drinking normally?'
    ]
}


class IntakeAgent:
    """
    Adaptive symptom intake agent.

    Conducts a structured, multi-turn conversation to collect:
      - Pet profile (species, breed, age, weight, name)
      - Chief complaint (primary symptom description)
      - Symptom details (area-specific information)
      - Timeline (when symptoms started, progression)
      - Context (eating/drinking, energy level, possible triggers)

    The agent adapts its follow-up questions based on:
      - Species (dogs vs cats vs exotic have different common conditions)
      - Symptom area (GI vs respiratory vs injury need different details)
      - What information has already been collected
    """

    def __init__(self):
        """Initialize the Intake Agent."""
        self.agent_name = 'intake'

    def process(self, session: dict, user_message: str) -> dict:
        # LLM-powered structured extraction. Uses gpt-4o-mini; response must be valid JSON. (Syed Ali Turab, Mar 4, 2026)
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        lang_code = session.get('language', 'en')
        lang_names = {
            'en': 'English', 'fr': 'French', 'zh': 'Chinese (Mandarin)',
            'ar': 'Arabic', 'es': 'Spanish', 'hi': 'Hindi', 'ur': 'Urdu'
        }
        lang_name = lang_names.get(lang_code, 'English')

        # System prompt enforces: collect facts only, never name conditions or prescribe.
        # intake_complete true only when both species and chief_complaint are present.
        system_prompt = f"""You are a veterinary intake assistant. Your ONLY job is to collect symptom information from a pet owner through structured questions. You are NOT a veterinarian.

HARD RULES — never violate under any circumstances:
1. NEVER name a disease, condition, or diagnosis (never say parvovirus, pancreatitis, diabetes, cancer, infection, gastroenteritis, kidney failure, or any medical condition name)
2. NEVER suggest a specific medication, supplement, or dosage
3. NEVER say "your pet has", "this sounds like", "this is probably", "this could be", or "this indicates [condition]"
4. ONLY collect observable facts: species, symptoms as described by owner, duration, eating/drinking status, energy level
5. Do NOT comment on urgency — that is handled by a separate system
6. Always respond in {lang_name}. JSON keys must remain in English.
7. Respond ONLY with valid JSON — no markdown, no preamble, no explanation outside the JSON

Respond with exactly this structure:
{{
  "pet_profile": {{"species": "", "pet_name": "", "breed": "", "age": "", "weight": ""}},
  "chief_complaint": "",
  "symptom_details": {{"area": "", "timeline": "", "eating_drinking": "", "energy_level": "", "additional": ""}},
  "follow_up_questions": [],
  "intake_complete": false
}}

Rules for intake_complete:
- Set to true ONLY when BOTH species AND chief_complaint are populated
- If either is missing, add exactly ONE follow-up question in follow_up_questions asking for that specific missing field
- Once both are known, set intake_complete to true even if optional fields are empty
- follow_up_questions must contain at most 1 question at a time

For the area field use one of: gastrointestinal, respiratory, dermatological, injury, urinary, neurological, behavioral — or leave empty if unclear."""

        # Build conversation history for context (user + assistant turns only).
        history = []
        for msg in session.get('messages', []):
            if msg.get('role') in ('user', 'assistant'):
                history.append({'role': msg['role'], 'content': msg.get('content', '')})
        history.append({'role': 'user', 'content': user_message})

        try:
            # Call OpenAI; strip markdown fences if present, then parse JSON.
            resp = client.chat.completions.create(
                model='gpt-4o-mini',
                max_tokens=600,
                temperature=0.2,
                messages=[{'role': 'system', 'content': system_prompt}] + history
            )
            raw = resp.choices[0].message.content.strip().replace('```json', '').replace('```', '').strip()
            parsed = json.loads(raw)

            # Extract fields from LLM response; update session for downstream agents and summary.
            pet_profile = parsed.get('pet_profile', {})
            symptom_details = parsed.get('symptom_details', {})
            chief_complaint = parsed.get('chief_complaint', '')
            intake_complete = bool(parsed.get('intake_complete', False))
            follow_up_questions = parsed.get('follow_up_questions', [])

            if pet_profile.get('species'):
                session.setdefault('pet_profile', {}).update({k: v for k, v in pet_profile.items() if v})
            if chief_complaint:
                session.setdefault('symptoms', {})['chief_complaint'] = chief_complaint
            for k, v in symptom_details.items():
                if v:
                    session.setdefault('symptoms', {})[k] = v

            # If LLM did not set follow_up_questions but intake not complete, add default by missing field.
            if not intake_complete and not follow_up_questions:
                if not session.get('pet_profile', {}).get('species'):
                    follow_up_questions = ['What type of pet do you have? (dog, cat, or other)']
                elif not chief_complaint:
                    follow_up_questions = ['Can you describe the main symptom or concern you are seeing?']

            return {
                'agent_name': self.agent_name,
                'status': 'success',
                'output': {
                    'pet_profile': session.get('pet_profile', {}),
                    'chief_complaint': chief_complaint or session.get('symptoms', {}).get('chief_complaint', ''),
                    'symptom_details': symptom_details,
                    'species': session.get('pet_profile', {}).get('species', ''),
                    'follow_up_questions': follow_up_questions,
                    'intake_complete': intake_complete
                },
                'confidence': 0.85 if intake_complete else 0.5,
                'warnings': []
            }

        except Exception as e:
            # Fallback: use session state and user_message; do not block pipeline. (Syed Ali Turab, Mar 4, 2026)
            logger.error(f'Intake LLM error: {e}')
            species = session.get('pet_profile', {}).get('species', '')
            complaint = session.get('symptoms', {}).get('chief_complaint', '')
            complete = bool(species and complaint)
            fq = []
            if not species:
                fq = ['What type of pet do you have? (dog, cat, or other)']
            elif not complaint:
                fq = ['Can you describe the main symptom you are concerned about?']
            return {
                'agent_name': self.agent_name,
                'status': 'success',
                'output': {
                    'pet_profile': session.get('pet_profile', {}),
                    'chief_complaint': user_message,
                    'symptom_details': {},
                    'species': session.get('pet_profile', {}).get('species', ''),
                    'follow_up_questions': fq,
                    'intake_complete': complete
                },
                'confidence': 0.3,
                'warnings': [f'Intake LLM failed, fallback used: {str(e)}']
            }
