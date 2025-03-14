# target/views.py
from django.shortcuts import render

def index(request):
    return render(request, 'technical/index.html')  # target/templates/target/index.html