from app.modulos.identidad import directorio as D


def test_nombre_desde_correo():
    assert D.nombre_desde_correo("analista-administrativo1@bateriasecuador.com") == "Analista Administrativo"
    assert D.nombre_desde_correo("juan.perez@bateriasecuador.com") == "Juan Perez"
    assert D.nombre_desde_correo("sistemas2@bateriasecuador.com") == "Sistemas"
    assert D.nombre_desde_correo("123@x.com") is None


def test_google_deshabilitado_sin_configuracion():
    assert D.nombre_en_google("juan.perez@bateriasecuador.com") is None
