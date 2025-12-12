from django.core.management.base import BaseCommand
from trending.models import Analyst

class Command(BaseCommand):
    help = 'Initialize the 14 analysts for the Trending app'

    def handle(self, *args, **options):
        macro_analysts = [
            ("Jan Hatzius", "Goldman Sachs"),
            ("Bruce C. Kasman", "JPMorgan"),
            ("Nathan Sheets", "Citi"),
            ("Seth Carpenter", "Morgan Stanley"),
            ("Mark Zandi", "Moody’s Analytics"),
            ("Neil Shearing", "Capital Economics"),
            ("Ellen Zentner", "Morgan Stanley Wealth Management"),
        ]

        stock_analysts = [
            ("David Kostin", "Goldman Sachs"),
            ("Savita Subramanian", "BofA"),
            ("Dubravko Lakos-Bujas", "J.P. Morgan"),
            ("Bankim “Binky” Chadha", "Deutsche Bank"),
            ("Mike Wilson", "Morgan Stanley"),
            ("Edward Yardeni", "Yardeni Research"),
            ("Lori Calvasina", "RBC Capital Markets"),
        ]

        # Function to create or get analyst
        for name, affiliation in macro_analysts:
            Analyst.objects.get_or_create(
                name=name,
                defaults={
                    'affiliation': affiliation,
                    'category': 'macro',
                    'score': 3
                }
            )
            self.stdout.write(self.style.SUCCESS(f'Checked/Created Macro Analyst: {name}'))

        for name, affiliation in stock_analysts:
            Analyst.objects.get_or_create(
                name=name,
                defaults={
                    'affiliation': affiliation,
                    'category': 'stock',
                    'score': 3
                }
            )
            self.stdout.write(self.style.SUCCESS(f'Checked/Created Stock Analyst: {name}'))

        self.stdout.write(self.style.SUCCESS('Successfully initialized all analysts.'))
