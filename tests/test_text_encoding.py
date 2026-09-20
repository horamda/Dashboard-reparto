from io import BytesIO
from text_encoding import repair_text
import pipeline


def test_known_damage_and_no_guessing():
    assert repair_text('REG PIZZER??A') == 'REG PIZZERÍA'
    assert repair_text('REG CAF?? / CONF') == 'REG CAFÉ / CONF'
    assert repair_text('BASE EDUCACI??N') == 'BASE EDUCACIÓN'
    assert repair_text('DirecciÃ³n y Mar de Ajó') == 'Dirección y Mar de Ajó'
    assert repair_text('¿Café? CLIENTE X??Y') == '¿Café? CLIENTE X??Y'


def test_customer_csv_encodings():
    csv = 'Cliente;Nombre de fantasia;Descripcion subcanal;Dirección;Horario de entrega\n287;CAFÉ;REG PIZZERÍA;Av. Colón;09:00 A 13:00\n'
    for encoding in ['utf-8-sig', 'cp1252']:
        row = pipeline.procesar_clientes(BytesIO(csv.encode(encoding)))['287']
        assert row['nombre'] == 'CAFÉ'
        assert row['direccion'] == 'Av. Colón'
        assert row['raw_cliente']['Descripcion subcanal'] == 'REG PIZZERÍA'
