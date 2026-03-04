# 🗺️ Maršruto KM Skaičiuoklė

Paprastas įrankis maršruto atstumo skaičiavimui pagal adresų sąrašą.

## Naudojimas

1. Įklijuokite adresus (vienas per eilutę)
2. Pasirinkite palyginimui kliento nurodytus km
3. Spauskite „Skaičiuoti"
4. Gaukite: atstumai tarp stotelių, bendras km, žemėlapis

## Paleisti lokaliai

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Konfigūracija

`.env` faile įrašykite:
```
AZURE_MAPS_KEY=jūsų_raktas
```

## Streamlit Cloud

Secrets skyriuje pridėkite:
```
AZURE_MAPS_KEY = "jūsų_raktas"
```
