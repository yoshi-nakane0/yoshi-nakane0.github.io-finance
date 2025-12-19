# dashboard/views.py

from django.shortcuts import render, get_object_or_404
from .models import AiAnalysis

def index(request):
    return render(request, 'dashboard/index.html')

def test1_index(request):
    analyses = AiAnalysis.objects.all()
    return render(request, 'dashboard/test1_list.html', {'analyses': analyses})

def test1_detail(request, pk):
    analysis = get_object_or_404(AiAnalysis, pk=pk)
    return render(request, 'dashboard/test1_detail.html', {'analysis': analysis})
