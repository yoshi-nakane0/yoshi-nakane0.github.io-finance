# dashboard/views.py

from django.shortcuts import render, get_object_or_404
from .models import AiAnalysis

def index(request):
    return render(request, 'dashboard/index.html')

def prediction_list(request):
    analyses = AiAnalysis.objects.all()
    return render(request, 'list.html', {'analyses': analyses})

def prediction_detail(request, pk):
    analysis = get_object_or_404(AiAnalysis, pk=pk)
    return render(request, 'detail.html', {'analysis': analysis})

def test1_index(request):
    return prediction_list(request)

def test1_detail(request, pk):
    return prediction_detail(request, pk)
