# explanation/views.py
from django.shortcuts import render

def index(request):
    return render(request, 'explanation/index.html')

def us_macro_gdp(request):
    return render(request, 'explanation/us_macro_gdp.html')

def us_macro_ism(request):
    return render(request, 'explanation/us_macro_ism.html')

def us_macro_pmi(request):
    return render(request, 'explanation/us_macro_pmi.html')

def us_macro_ppi(request):
    return render(request, 'explanation/us_macro_ppi.html')

def us_macro_sales(request):
    return render(request, 'explanation/us_macro_sales.html')
