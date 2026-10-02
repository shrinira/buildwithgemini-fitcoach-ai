import os
import google.auth
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-90bea91b6dcb"

def seed_database():
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    db = firestore.Client(project=PROJECT_ID, credentials=credentials)
    print(f"Connecting to Firestore with hardcoded project ID: {PROJECT_ID}")

    workouts = [
        {
            "id": "upper_body_power",
            "title": "Upper Body Power & Strength",
            "category": "Strength",
            "difficulty": "Intermediate",
            "duration_minutes": 45,
            "target_muscles": ["Chest", "Back", "Shoulders", "Triceps"],
            "equipment_needed": ["Dumbbells", "Bench"],
            "description": "Focus on explosive power and strength building for upper body pushing and pulling exercises.",
            "exercises": [
                {"name": "Dumbbell Bench Press", "sets": 4, "reps": "8-10"},
                {"name": "Bent-Over Dumbbell Rows", "sets": 4, "reps": "8-10"},
                {"name": "Overhead Shoulder Press", "sets": 3, "reps": "10-12"},
                {"name": "Incline Dumbbell Flyes", "sets": 3, "reps": "12"}
            ]
        },
        {
            "id": "full_body_hiit",
            "title": "High-Intensity Full Body Burn",
            "category": "HIIT",
            "difficulty": "Advanced",
            "duration_minutes": 30,
            "target_muscles": ["Full Body", "Core", "Quads"],
            "equipment_needed": ["Bodyweight", "Kettlebell"],
            "description": "Fast-paced interval training designed to boost cardiovascular endurance and burn calories.",
            "exercises": [
                {"name": "Kettlebell Swings", "sets": 4, "reps": "40 secs work / 20 secs rest"},
                {"name": "Burpees", "sets": 4, "reps": "40 secs work / 20 secs rest"},
                {"name": "Mountain Climbers", "sets": 4, "reps": "40 secs work / 20 secs rest"},
                {"name": "Jump Squats", "sets": 4, "reps": "40 secs work / 20 secs rest"}
            ]
        },
        {
            "id": "core_and_mobility",
            "title": "Core Stability & Joint Mobility",
            "category": "Mobility",
            "difficulty": "Beginner",
            "duration_minutes": 25,
            "target_muscles": ["Abs", "Lower Back", "Hips"],
            "equipment_needed": ["Yoga Mat"],
            "description": "Low-impact routine focusing on core engagement, hip opening, and spinal alignment.",
            "exercises": [
                {"name": "Plank Hold", "sets": 3, "reps": "45-60 seconds"},
                {"name": "Bird-Dog", "sets": 3, "reps": "10 per side"},
                {"name": "World's Greatest Stretch", "sets": 3, "reps": "5 per side"},
                {"name": "Glute Bridges", "sets": 3, "reps": "15"}
            ]
        },
        {
            "id": "leg_day_hypertrophy",
            "title": "Lower Body Sculpt & Build",
            "category": "Strength",
            "difficulty": "Intermediate",
            "duration_minutes": 50,
            "target_muscles": ["Quads", "Hamstrings", "Glutes", "Calves"],
            "equipment_needed": ["Dumbbells", "Resistance Band"],
            "description": "Comprehensive leg workout focusing on quad isolation, hip hinge patterns, and glute activation.",
            "exercises": [
                {"name": "Goblet Squats", "sets": 4, "reps": "10-12"},
                {"name": "Romanian Deadlifts", "sets": 4, "reps": "10-12"},
                {"name": "Walking Lunges", "sets": 3, "reps": "12 steps per leg"},
                {"name": "Banded Calf Raises", "sets": 4, "reps": "15-20"}
            ]
        }
    ]

    for workout in workouts:
        doc_id = workout["id"]
        db.collection("workouts").document(doc_id).set(workout)
        print(f"Seeded workout: {workout['title']} ({doc_id})")

    print("\nFirestore seeding complete!")

if __name__ == "__main__":
    seed_database()
