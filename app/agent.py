# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

from google.cloud import firestore, storage
from google.genai import Client, types

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.memory import VertexAiMemoryBankService
from google.adk.code_executors import AgentEngineSandboxCodeExecutor

from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
from .a2ui_utils import a2ui_callback

# Load local .env variables
load_dotenv()

MODEL = "gemini-2.5-flash"

# Hardcoded GCP Project ID, Location, and Storage Bucket
PROJECT_ID = "qwiklabs-gcp-03-90bea91b6dcb"
LOCATION = "us-east1"
BUCKET_NAME = "fitcoach-ai-media-qwiklabs-gcp-03-90bea91b6dcb"
MEMORY_BANK_ID = "7944139735960649728"

# Memory Bank Service builder for deployment
def memory_service_builder():
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location=LOCATION,
        agent_engine_id=MEMORY_BANK_ID,
    )

# WRITE: after each turn, send the session to Memory Bank for memory extraction
async def generate_memories_callback(callback_context: CallbackContext):
    """Sends the session's events to Memory Bank for extraction."""
    await callback_context.add_session_to_memory()
    return None

# Initialize Agent Engine Sandbox Code Executor from deployment_metadata.json
DEPLOYMENT_METADATA_PATH = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
code_executor = None

if os.path.exists(DEPLOYMENT_METADATA_PATH):
    try:
        with open(DEPLOYMENT_METADATA_PATH, "r") as f:
            metadata = json.load(f)
        agent_engine_id = metadata.get("remote_agent_runtime_id")
        if agent_engine_id:
            code_executor = AgentEngineSandboxCodeExecutor(
                agent_engine_resource_name=agent_engine_id
            )
            print(f"Configured AgentEngineSandboxCodeExecutor with resource: {agent_engine_id}")
    except Exception as e:
        print(f"Warning: Failed to load deployment_metadata.json for code_executor: {e}")


def get_firestore_client() -> firestore.Client:
    """Helper function to return a Firestore client using hardcoded project ID."""
    return firestore.Client(project=PROJECT_ID)


def list_workouts(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves available workout routines from the Firestore database.

    Args:
        category: Optional category filter (e.g. 'Strength', 'HIIT', 'Mobility').

    Returns:
        List of workout summary dictionaries containing workout ID, title, category, duration, and difficulty.
    """
    db = get_firestore_client()
    query = db.collection("workouts")
    
    if category:
        query = query.where("category", "==", category)

    docs = query.stream()
    results = []
    for doc in docs:
        data = doc.to_dict()
        results.append({
            "id": doc.id,
            "title": data.get("title", ""),
            "category": data.get("category", ""),
            "difficulty": data.get("difficulty", ""),
            "duration_minutes": data.get("duration_minutes", 0),
            "target_muscles": data.get("target_muscles", []),
            "description": data.get("description", ""),
        })
    return results


def get_workout_details(workout_id: str) -> Dict[str, Any]:
    """Retrieves full details and exercises for a specific workout from Firestore.

    Args:
        workout_id: The unique identifier for the workout (e.g., 'upper_body_power').

    Returns:
        A dictionary with complete workout details and exercise breakdown, or an error message if not found.
    """
    db = get_firestore_client()
    doc_ref = db.collection("workouts").document(workout_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Workout with ID '{workout_id}' not found."}

    return doc.to_dict()


def log_completed_workout(workout_id: str, duration_minutes: int, notes: str = "") -> Dict[str, Any]:
    """Logs a completed workout session into the user's Firestore workout history.

    Args:
        workout_id: The ID of the workout completed.
        duration_minutes: Actual duration spent in minutes.
        notes: Optional user notes or feedback on the workout session.

    Returns:
        A confirmation dictionary containing log ID, timestamp, and details.
    """
    db = get_firestore_client()
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    log_entry = {
        "workout_id": workout_id,
        "duration_minutes": duration_minutes,
        "notes": notes,
        "completed_at": timestamp
    }
    
    update_time, doc_ref = db.collection("workout_logs").add(log_entry)
    
    return {
        "status": "success",
        "log_id": doc_ref.id,
        "workout_id": workout_id,
        "duration_minutes": duration_minutes,
        "notes": notes,
        "completed_at": timestamp
    }


def lookup_ingredient_nutrition(ingredient_name: str) -> Dict[str, Any]:
    """Queries the WGER free public API (from public-apis directory) for real ingredient macronutrient data.

    Args:
        ingredient_name: The name of the ingredient or food item (e.g., 'egg', 'banana', 'cottage cheese').

    Returns:
        A dictionary containing real nutritional facts (calories, protein, carbohydrates, fat).
    """
    api_key = os.environ.get("WGER_API_KEY")
    headers = {"User-Agent": "FitCoachAI/1.0"}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"
        
    url = f"https://wger.de/api/v2/ingredient/?name={urllib.parse.quote(ingredient_name)}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            results = data.get("results", [])
            if not results:
                return {"error": f"No nutrition info found for '{ingredient_name}' in WGER API."}
            
            top_match = results[0]
            return {
                "ingredient": top_match.get("name"),
                "energy_kcal": top_match.get("energy"),
                "protein_g": top_match.get("protein"),
                "carbohydrates_g": top_match.get("carbohydrates"),
                "fat_g": top_match.get("fat"),
                "source": "WGER Open Fitness & Nutrition API"
            }
    except Exception as e:
        return {"error": f"Failed to fetch nutrition data from WGER API: {str(e)}"}


def get_population_fitness_percentile(weekly_active_minutes: int, age: int = 30) -> Dict[str, Any]:
    """Evaluates weekly exercise activity against CDC/WHO population benchmarks, returning percentile rank, badge level, and health safety limits.

    Args:
        weekly_active_minutes: Total minutes of physical activity completed per week.
        age: User's age in years (default 30).

    Returns:
        A dictionary with percentile ranking, recommended badge tier, and overtraining safety guidance.
    """
    base_median = 150.0
    if age >= 60:
        base_median = 120.0
    elif age <= 25:
        base_median = 180.0

    percentile = int(min(99, max(5, (weekly_active_minutes / (base_median * 2.5)) * 100)))

    if percentile >= 80:
        badge_tier = "Gold Elite Badge (Top 20% Active)"
    elif percentile >= 60:
        badge_tier = "Silver Vanguard Badge (Top 40% Active)"
    elif percentile >= 40:
        badge_tier = "Bronze Pacesetter Badge (Top 60% Active)"
    else:
        badge_tier = "Starter Motivator Badge"

    overtraining_warning = None
    if weekly_active_minutes > 450:
        overtraining_warning = (
            "Caution: You are exercising over 7.5 hours per week! "
            "Ensure adequate rest, hydration, and recovery to prevent overtraining and injury."
        )

    return {
        "weekly_active_minutes": weekly_active_minutes,
        "age": age,
        "population_percentile": f"Better than ~{percentile}% of population in your age group",
        "recommended_badge_tier": badge_tier,
        "health_guideline_status": "Exceeds WHO 150-min weekly recommendation" if weekly_active_minutes >= 150 else "Below WHO recommendation",
        "safety_note": overtraining_warning or "Great balance of healthy physical activity!"
    }


def geocode_address(address: str) -> Dict[str, Any]:
    """Uses Google Maps Geocoding API to convert a location or street address into latitude and longitude coordinates.

    Args:
        address: The address or place name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A dictionary containing formatted address, latitude, and longitude.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY is not set in environment."}

    geocode_url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
    try:
        req = urllib.request.Request(geocode_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            geo_data = json.loads(resp.read().decode())
            results = geo_data.get("results", [])
            if not results:
                return {"error": f"Address '{address}' could not be geocoded."}
            
            top_result = results[0]
            loc = top_result.get("geometry", {}).get("location", {})
            return {
                "address": top_result.get("formatted_address"),
                "location": {
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng")
                }
            }
    except Exception as e:
        return {"error": f"Geocoding API call failed: {str(e)}"}


def find_nearby_fitness_places(address: str, place_type: str = "gym", radius_meters: float = 5000.0) -> Dict[str, Any]:
    """Uses Google Maps Places API (New) to search for nearby fitness places (e.g. gyms, sports centers, parks) around a given address.

    Args:
        address: Starting address or location name.
        place_type: Type of place to search for (e.g., 'gym', 'fitness_center', 'park', 'sports_complex').
        radius_meters: Search radius in meters (default 5000m / 5km).

    Returns:
        A dictionary containing the search center and a list of key place details (name, formatted address, location).
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY is not set in environment."}

    geo_res = geocode_address(address)
    if "error" in geo_res:
        return geo_res

    center_lat = geo_res["location"]["latitude"]
    center_lng = geo_res["location"]["longitude"]

    places_url = "https://places.googleapis.com/v1/places:searchNearby"
    body_data = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": center_lat,
                    "longitude": center_lng
                },
                "radius": float(radius_meters)
            }
        }
    }
    
    req_body = json.dumps(body_data).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"
    }

    try:
        req = urllib.request.Request(places_url, data=req_body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            places_data = json.loads(resp.read().decode())
            raw_places = places_data.get("places", [])
            
            clean_places = []
            for p in raw_places:
                clean_places.append({
                    "name": p.get("displayName", {}).get("text", "Unknown"),
                    "address": p.get("formattedAddress", "N/A"),
                    "location": p.get("location", {})
                })
            
            return {
                "search_center": geo_res["address"],
                "place_type": place_type,
                "radius_meters": radius_meters,
                "places": clean_places
            }
    except Exception as e:
        return {"error": f"Places API (New) request failed: {str(e)}"}


def generate_workout_badge(achievement_title: str) -> Dict[str, Any]:
    """Generates a custom 3D motivational workout badge image for an achievement and uploads it to public Cloud Storage.

    Args:
        achievement_title: The name of the achievement (e.g. 'Leg Day Crusher', 'Silver Vanguard', '10K Steps Master').

    Returns:
        A dictionary containing the status, achievement title, and public image URL.
    """
    try:
        genai_client = Client(vertexai=True, project=PROJECT_ID, location="us-central1")
        prompt = (
            f"A high-quality 3D athletic medal badge for workout achievement: '{achievement_title}', "
            "vibrant metallic emblem style, glossy finish, fitness theme, dark background."
        )
        
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt,
        )
        
        image_bytes = None
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            return {"error": f"Failed to generate image for achievement '{achievement_title}'."}

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        safe_name = achievement_title.lower().replace(" ", "_").replace("/", "_")
        blob_name = f"badges/{safe_name}_{uuid.uuid4().hex[:6]}.png"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"
        return {
            "status": "success",
            "achievement": achievement_title,
            "image_url": public_url
        }
    except Exception as e:
        return {"error": f"Badge generation error: {str(e)}"}


def generate_fitness_meal_image(meal_description: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Generates an appetizing image for a fitness meal/recipe item using gemini-3.1-flash-lite-image in the global region.
    Saves the image as a session artifact and uploads it to public Cloud Storage.

    Args:
        meal_description: Description of the fitness meal or dish (e.g. 'Grilled salmon with quinoa and roasted asparagus').
        tool_context: ADK ToolContext used to save session artifacts.

    Returns:
        A dictionary containing the status, description, artifact filename, and public GCS image URL.
    """
    try:
        genai_client = Client(vertexai=True, project=PROJECT_ID, location="global")
        prompt = (
            f"A high-quality, professional food photograph of fitness meal: {meal_description}, "
            "appetizing presentation, studio lighting, top-down angle, health food concept."
        )
        
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
        )
        
        image_bytes = None
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            return {"error": f"Failed to generate meal image for '{meal_description}'."}

        safe_filename = f"meal_{uuid.uuid4().hex[:6]}.png"
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        tool_context.save_artifact(safe_filename, artifact_part)

        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob_name = f"meals/{safe_filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{blob_name}"

        return {
            "status": "success",
            "meal_description": meal_description,
            "artifact_filename": safe_filename,
            "public_image_url": public_url
        }
    except Exception as e:
        return {"error": f"Meal image generation failed: {str(e)}"}


# Build A2UI System Prompt using A2uiSchemaManager version 0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are FitCoach AI, a personal fitness and workout assistant. "
        "You help users discover workout routines, view exercise guides, track completed workouts in Firestore, "
        "look up ingredient nutrition specs, evaluate population activity percentiles with health safety limits, "
        "find nearby gyms and fitness centers using Google Maps Geocoding & Places APIs, "
        "and generate custom 3D workout badges or healthy fitness meal images to celebrate their achievements. "
        "You remember the user's stated preferences, fitness history, and specifically track and remember all user injuries, physical limitations, joint pain, surgeries, or recovery conditions across conversations to ensure all exercise recommendations are safe, modified, and low-impact when necessary."
    ),
    workflow_description="Analyze the user's request, query necessary tools or database records, and return structured A2UI cards or text when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="fitcoach_ai",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        list_workouts,
        get_workout_details,
        log_completed_workout,
        lookup_ingredient_nutrition,
        get_population_fitness_percentile,
        geocode_address,
        find_nearby_fitness_places,
        generate_workout_badge,
        generate_fitness_meal_image
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    code_executor=code_executor,
)

app = App(
    root_agent=root_agent,
    name="app",
)
