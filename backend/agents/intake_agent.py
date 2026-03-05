"""
Sub-Agent A: Intake Agent

Authors: Syed Ali Turab, Fergie Feng & Diana Liu | Team: Broadview
Date:   March 1, 2026
Code updated: Syed Ali Turab, March 4, 2026 — LLM-powered intake 
with diagnosis guardrails.

Collects pet profile, chief complaint, and symptom details through
adaptive, multi-turn follow-up questions tailored to species and 
symptom area.
"""

import os
import json
import logging
import openai

logger = logging.getLogger('petcare.agents.intake')

REQUIRED_FIELDS = ['species', 'chief_complaint']
OPTIONAL_FIELDS = ['pet_name', 'breed', 'age', 'weight',
                   'timeline', 'eating_drinking', 'energy_level']


class IntakeAgent:
    """Adaptive LLM-powered symptom intake agent."""

    def __init__(self):
        self.agent_name = 'intake'

    def process(self, session: dict, user_message: str) -> dict:
        """
        Extract structured intake data from owner message via LLM.
        Sets intake_complete=True as soon as species + chief_complaint
        are both known. Never diagnoses or names conditions.
        Falls back to simple extraction on LLM failure.
        """
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        lang_code = session.get('language', 'en')
        lang_names = {
            'en': 'English', 'fr': 'French', 'zh': 'Chinese (Mandarin)',
            'ar': 'Arabic', 'es': 'Spanish', 'hi': 'Hindi', 'ur': 'Urdu'
        }
        lang_name = lang_names.get(lang_code, 'English')

        # Build what we already know so LLM does not re-ask for it
        known_species = session.get('pet_profile', {}).get('species', '')
        known_complaint = session.get('symptoms', {}).get('chief_complaint', '')

        system_prompt = f"""You are a veterinary intake assistant. Collect symptom information from a pet owner.

HARD RULES — never violate:
1. NEVER name a disease, condition, or diagnosis
2. NEVER suggest medications or dosages
3. NEVER say "your pet has", "this sounds like", "this could be"
4. ONLY collect facts: species, symptoms owner describes, duration, eating/drinking, energy
5. Do NOT comment on urgency
6. Respond in {lang_name}. JSON keys must stay in English.
7. Respond ONLY with valid JSON — no markdown fences, no text outside the JSON

Already collected — do NOT ask for these again:
- species: "{known_species}"
- chief_complaint: "{known_complaint}"

Respond with EXACTLY this JSON structure (all keys required, use empty string if unknown):
{{
  "pet_profile": {{"species": "", "pet_name": "", "breed": "", "age": "", "weight": ""}},
  "chief_complaint": "",
  "symptom_details": {{"area": "", "timeline": "", "eating_drinking": "", "energy_level": "", "additional": ""}},
  "follow_up_questions": [],
  "intake_complete": false
}}

CRITICAL rules for intake_complete and follow_up_questions:
- Set intake_complete to true when BOTH species AND chief_complaint are known (including from prior messages)
- If species is already known ("{known_species}") count it as collected — do NOT ask again
- If chief_complaint is already known ("{known_complaint}") count it as collected — do NOT ask again  
- When intake_complete is true, follow_up_questions must be an empty list []
- When intake_complete is false, follow_up_questions must contain exactly ONE plain string question
- follow_up_questions must be a list of strings, NOT a list of objects
- WRONG: [{{"question": "How old?"}}]
- RIGHT: ["How old is your pet?"]

For symptom_details.area use one of: gastrointestinal, respiratory, 
dermatological, injury, urinary, neurological, behavioral — or empty string."""

        history = []
        for msg in session.get('messages', []):
            if msg.get('role') in ('user', 'assistant'):
                content = msg.get('content', '')
                # Flatten assistant messages that were stored as dicts
                if isinstance(content, dict):
                    content = str(content)
                history.append({'role': msg['role'], 'content': content})
        history.append({'role': 'user', 'content': user_message})

        try:
            resp = client.chat.completions.create(
                model='gpt-4o-mini',
                max_tokens=600,
                temperature=0.1,
                messages=[{'role': 'system', 'content': system_prompt}] + history
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown fences if present
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            raw = raw.strip()

            parsed = json.loads(raw)

            pet_profile = parsed.get('pet_profile', {})
            symptom_details = parsed.get('symptom_details', {})
            chief_complaint = parsed.get('chief_complaint', '') or known_complaint
            intake_complete = bool(parsed.get('intake_complete', False))
            follow_up_questions = parsed.get('follow_up_questions', [])

            # Flatten any dict questions the LLM returned despite instructions
            cleaned_questions = []
            for q in follow_up_questions:
                if isinstance(q, dict):
                    # Extract string value from common dict shapes
                    cleaned_questions.append(
                        q.get('question') or q.get('text') or str(q)
                    )
                elif isinstance(q, str) and q.strip():
                    cleaned_questions.append(q.strip())

            # Merge species into session
            species = (pet_profile.get('species') or known_species or '').lower()
            if species:
                session.setdefault('pet_profile', {})['species'] = species
                pet_profile['species'] = species

            # Merge other profile fields
            for k, v in pet_profile.items():
                if v and k != 'species':
                    session.setdefault('pet_profile', {})[k] = v

            # Merge complaint and symptom details
            if chief_complaint:
                session.setdefault('symptoms', {})['chief_complaint'] = chief_complaint
            for k, v in symptom_details.items():
                if v:
                    session.setdefault('symptoms', {})[k] = v

            # Re-check intake_complete using session state
            # (LLM may have missed that prior messages already gave us what we need)
            final_species = session.get('pet_profile', {}).get('species', '')
            final_complaint = session.get('symptoms', {}).get('chief_complaint', '')
            if final_species and final_complaint:
                intake_complete = True
                cleaned_questions = []

            # Safety: if not complete and no question, generate one
            if not intake_complete and not cleaned_questions:
                if not final_species:
                    cleaned_questions = ['What type of pet do you have? (dog, cat, or other)']
                elif not final_complaint:
                    cleaned_questions = ['Can you describe the main symptom you are concerned about?']

            return {
                'agent_name': self.agent_name,
                'status': 'success',
                'output': {
                    'pet_profile': session.get('pet_profile', {}),
                    'species': session.get('pet_profile', {}).get('species', ''),
                    'chief_complaint': final_complaint,
                    'symptom_details': symptom_details,
                    'follow_up_questions': cleaned_questions,
                    'intake_complete': intake_complete
                },
                'confidence': 0.85 if intake_complete else 0.5,
                'warnings': []
            }

        except Exception as e:
            logger.error(f'Intake LLM error: {e}')
            # Fallback: use what we already know from session
            final_species = session.get('pet_profile', {}).get('species', '')
            final_complaint = session.get('symptoms', {}).get('chief_complaint', '') or user_message
            session.setdefault('symptoms', {})['chief_complaint'] = final_complaint
            complete = bool(final_species and final_complaint)
            fq = []
            if not final_species:
                fq = ['What type of pet do you have? (dog, cat, or other)']
            return {
                'agent_name': self.agent_name,
                'status': 'success',
                'output': {
                    'pet_profile': session.get('pet_profile', {}),
                    'species': final_species,
                    'chief_complaint': final_complaint,
                    'symptom_details': {},
                    'follow_up_questions': fq,
                    'intake_complete': complete
                },
                'confidence': 0.3,
                'warnings': [f'Intake LLM failed, fallback used: {str(e)}']
            }
