from django.shortcuts import render
from django.db.models import Avg
from .models import Analyst

def index(request):
    analysts = Analyst.objects.all()
    
    macro_analysts = analysts.filter(category='macro')
    stock_analysts = analysts.filter(category='stock')
    
    # Calculate averages
    avg_score = analysts.aggregate(Avg('score'))['score__avg'] or 0
    macro_avg = macro_analysts.aggregate(Avg('score'))['score__avg'] or 0
    stock_avg = stock_analysts.aggregate(Avg('score'))['score__avg'] or 0
    
    # Calculate percentages for charts (score / 5 * 100)
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
