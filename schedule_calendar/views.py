# schedule/views.py

from django.shortcuts import render

def index(request):
    return render(request, 'schedule/index.html')  # schedule/templates/schedule/index.html