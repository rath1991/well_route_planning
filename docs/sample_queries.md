# Sample Queries

## Data Queries (LLM-to-SQL)

### Production
- "What is the average oil production across all wells?"
- "Which well has the highest oil production?"
- "Show me wells producing more than 400 barrels per day"
- "What is the total liquid production across all wells?"

### Water Cut
- "Which wells have water cut above 80%?"
- "List wells with the highest water cut"
- "What is the average water cut across all wells?"

### Reliability & Issues
- "How many wells have reliability issues?"
- "What are the most common issue categories?"
- "Which wells have gas locking issues?"
- "Show me wells with electrical faults"
- "List wells with scale buildup problems"

### Priority & Ranking
- "Show me the top 5 wells by priority score"
- "Which well has the highest priority score?"
- "List wells with priority score above 80"
- "Rank all wells by urgency score"

### Workover & Action
- "Which wells need workover?"
- "What actions are required for the top priority wells?"
- "How many wells need frequency optimization?"

### Geography
- "List wells in Reeves county"
- "How many wells are in each county?"
- "Show me wells in New Mexico"

### Uplift & Confidence
- "Which well has the highest uplift potential?"
- "Show wells with uplift above 20 barrels per day"
- "Which wells have low confidence scores?"

### Visit Recency
- "Which wells haven't been visited in over 20 days?"
- "What is the average days since last visit?"
- "Show me the most overdue wells"

## Route Planning Queries

### Basic
- "Plan my visits for today"
- "Plan my visits for 6 hours. Max 6 stops."
- "Optimize my route for today, 8 hour budget"
- "Schedule field visits for 5 wells"

### With Constraints
- "Plan a route to visit the highest priority wells in 4 hours"
- "Where should I go today? I have 4 hours."
- "Plan a route with max 3 stops"
- "Schedule my visits for a half day"

### Specific
- "Plan a route to visit wells with gas locking issues"
- "Which wells should I visit first today?"
- "Optimize my field trip for the morning shift"
- "Plan my drive to the most urgent wells"

## curl Examples

```bash
# Seed database (run once)
curl -X POST http://localhost:8000/admin/seed

# Data query
curl -X POST http://localhost:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Which wells have water cut above 80%?"}'

# Route query (auto-detected via intent)
curl -X POST http://localhost:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Plan my visits for 6 hours. Max 6 stops."}'

# Route query with custom context
curl -X POST http://localhost:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Plan my visits",
    "context": {
      "start_location": {"name": "Field Office", "lat": 31.95, "lon": -103.10},
      "time_budget_minutes": 240,
      "max_stops": 5,
      "top_n_candidates": 12
    }
  }'

# Direct route endpoint (bypass intent detection)
curl -X POST http://localhost:8000/webhook/elevenlabs/route \
  -H "Content-Type: application/json" \
  -d '{"query": "Plan my visits", "time_budget_minutes": 360, "max_stops": 6}'

# Health check
curl http://localhost:8000/health
```
