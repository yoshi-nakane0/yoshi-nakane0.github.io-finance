# control/views.py
from django.shortcuts import render

def index(request):
    return render(request, 'control/index.html')

def us_macro_gdp(request):
    return render(request, 'control/us_macro_gdp.html')

def us_macro_ism(request):
    return render(request, 'control/us_macro_ism.html')

def us_macro_pmi(request):
    return render(request, 'control/us_macro_pmi.html')  # control/templates/control/index.html
