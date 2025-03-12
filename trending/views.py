# trending/views.py
from django.shortcuts import render

def index(request):
    return render(request, 'trending/index.html')  # trending/templates/trending/index.html