"""Independent integration identity and official Strava endpoints."""

DOMAIN = "ha_strava_automation"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ATHLETE_URL = "https://www.strava.com/api/v3/athlete"
READY_EVENT = "ha_strava_automation_activity_ready"
ERROR_EVENT = "ha_strava_automation_error"
WORKOUT_EVENT = "ha_strava_automation_workout_processed"
