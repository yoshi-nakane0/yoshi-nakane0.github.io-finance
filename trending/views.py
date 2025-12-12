from django.shortcuts import render
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from .models import Analyst

FALLBACK_ANALYSTS = [
    {"name": "Jan Hatzius", "affiliation": "Goldman Sachs", "category": "macro", "score": 3},
    {"name": "Bruce C. Kasman", "affiliation": "JPMorgan", "category": "macro", "score": 3},
    {"name": "Nathan Sheets", "affiliation": "Citi", "category": "macro", "score": 3},
    {"name": "Seth Carpenter", "affiliation": "Morgan Stanley", "category": "macro", "score": 3},
    {"name": "Mark Zandi", "affiliation": "Moody’s Analytics", "category": "macro", "score": 3},
    {"name": "Neil Shearing", "affiliation": "Capital Economics", "category": "macro", "score": 3},
    {
        "name": "Ellen Zentner",
        "affiliation": "Morgan Stanley Wealth Management",
        "category": "macro",
        "score": 3,
    },
    {"name": "David Kostin", "affiliation": "Goldman Sachs", "category": "stock", "score": 3},
    {"name": "Savita Subramanian", "affiliation": "BofA", "category": "stock", "score": 3},
    {"name": "Dubravko Lakos-Bujas", "affiliation": "J.P. Morgan", "category": "stock", "score": 3},
    {
        "name": "Bankim “Binky” Chadha",
        "affiliation": "Deutsche Bank",
        "category": "stock",
        "score": 3,
    },
    {"name": "Mike Wilson", "affiliation": "Morgan Stanley", "category": "stock", "score": 3},
    {"name": "Edward Yardeni", "affiliation": "Yardeni Research", "category": "stock", "score": 3},
    {"name": "Lori Calvasina", "affiliation": "RBC Capital Markets", "category": "stock", "score": 3},
]


def _avg_score(analysts):
    scores = []
    for analyst in analysts:
        score = analyst.get("score")
        if isinstance(score, (int, float)):
            scores.append(score)
    if not scores:
        return 0
    return sum(scores) / len(scores)


def index(request):
    try:
        analysts = list(
            Analyst.objects.all().values("name", "affiliation", "category", "score")
        )
    except (OperationalError, ProgrammingError, DatabaseError) as e:
        print(f"[trending] DB unavailable, using fallback data: {e}")
        analysts = FALLBACK_ANALYSTS

    macro_analysts = [a for a in analysts if a.get("category") == "macro"]
    stock_analysts = [a for a in analysts if a.get("category") == "stock"]

    avg_score = _avg_score(analysts)
    macro_avg = _avg_score(macro_analysts)
    stock_avg = _avg_score(stock_analysts)

    macro_percent = (macro_avg / 5) * 100
    stock_percent = (stock_avg / 5) * 100
    
    if avg_score >= 3:
        market_sentiment = "LONG"
        sentiment_color = "text-success" 
    else:
        market_sentiment = "SHORT"
        sentiment_color = "text-danger" 

    context = {
        'macro_analysts': macro_analysts,
        'stock_analysts': stock_analysts,
        'market_sentiment': market_sentiment,
        'sentiment_color': sentiment_color,
        'avg_score': round(avg_score, 2),
        'macro_avg': round(macro_avg, 2),
        'macro_percent': round(macro_percent, 1),
        'stock_avg': round(stock_avg, 2),
        'stock_percent': round(stock_percent, 1),
    }
    return render(request, 'trending/index.html', context)
