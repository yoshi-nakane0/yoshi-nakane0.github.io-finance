# target/views.py
from django.shortcuts import render

def index(request):
    return render(request, 'target/index.html')  # target/templates/target/index.html