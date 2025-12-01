"""
TOON Format - Conversión entre JSON y formato TOON.

Formato TOON es más compacto y legible que JSON:
- Arrays: array_name[count]{field1,field2,...}:
  value1,value2,...
  value3,value4,...
- Strings simples: field_name: value
"""

from typing import Dict, Any, List, Union


def dict_to_toon(data: Dict[str, Any]) -> str:
    """
    Convierte un diccionario a formato TOON.
    
    Args:
        data: Diccionario a convertir
        
    Returns:
        String en formato TOON
    """
    lines = []
    
    for key, value in data.items():
        if isinstance(value, list):
            if len(value) == 0:
                lines.append(f"{key}[0]{{}}:")
            elif isinstance(value[0], str):
                # Array de strings simples
                lines.append(f"{key}[{len(value)}]{{value}}:")
                for item in value:
                    lines.append(f"  {item}")
            elif isinstance(value[0], dict):
                # Array de objetos
                if value:
                    fields = list(value[0].keys())
                    lines.append(f"{key}[{len(value)}]{{{','.join(fields)}}}:")
                    for item in value:
                        values = [str(item.get(f, '')) for f in fields]
                        lines.append(f"  {','.join(values)}")
                else:
                    lines.append(f"{key}[0]{{}}:")
            else:
                # Array de valores primitivos
                lines.append(f"{key}[{len(value)}]{{value}}:")
                for item in value:
                    lines.append(f"  {item}")
        elif isinstance(value, dict):
            # Objeto anidado
            lines.append(f"{key}:")
            nested = dict_to_toon(value)
            for line in nested.split('\n'):
                if line.strip():
                    lines.append(f"  {line}")
        else:
            # Valor simple
            lines.append(f"{key}: {value}")
    
    return '\n'.join(lines)


def toon_to_dict(toon_text: str) -> Dict[str, Any]:
    """
    Parsea formato TOON a diccionario.
    
    Args:
        toon_text: Texto en formato TOON
        
    Returns:
        Diccionario con los datos parseados
    """
    result = {}
    lines = [line.rstrip() for line in toon_text.strip().split('\n') if line.strip()]
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Detectar array: array_name[count]{fields}:
        if '[' in line and '{' in line and ':' in line:
            # Parsear header del array
            array_match = line.split(':', 1)[0]
            name_part = array_match.split('[')[0]
            count_part = array_match.split('[')[1].split(']')[0]
            fields_part = array_match.split('{')[1].split('}')[0]
            
            array_name = name_part.strip()
            count = int(count_part)
            fields = [f.strip() for f in fields_part.split(',')] if fields_part else []
            
            # Leer valores del array
            array_values = []
            i += 1
            
            for j in range(count):
                if i < len(lines):
                    value_line = lines[i].strip()
                    # Saltar líneas vacías
                    if not value_line:
                        i += 1
                        continue
                    
                    # Si la línea no está indentada y no es parte del array, terminar
                    if not lines[i].startswith(' ') and not lines[i].startswith('\t'):
                        break
                    
                    if fields:
                        # Objeto con campos (valores separados por comas)
                        values = [v.strip() for v in value_line.split(',')]
                        
                        # Si solo hay un campo llamado "value", tratar como array de strings simples
                        if len(fields) == 1 and fields[0] == "value":
                            if values:
                                array_values.append(values[0])
                        else:
                            # Objeto con múltiples campos
                            obj = {}
                            for k, field in enumerate(fields):
                                if k < len(values):
                                    obj[field] = values[k]
                            array_values.append(obj)
                    else:
                        # Valor simple (string)
                        array_values.append(value_line)
                    i += 1
                else:
                    break
            
            result[array_name] = array_values
            continue
        
        # Detectar campo simple: field_name: value (solo si no es parte de un array)
        if ':' in line and not line.startswith(' ') and not line.startswith('\t'):
            # Verificar que no sea un array (no debe tener [ y { antes del :)
            if '[' not in line.split(':')[0] and '{' not in line.split(':')[0]:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    
                    # Intentar convertir a número o boolean
                    if value.lower() == 'true':
                        result[key] = True
                    elif value.lower() == 'false':
                        result[key] = False
                    elif value.isdigit():
                        result[key] = int(value)
                    elif value.replace('.', '', 1).isdigit():
                        result[key] = float(value)
                    else:
                        result[key] = value
            i += 1
            continue
        
        i += 1
    
    return result


def format_profile_to_toon(profile: Dict[str, Any]) -> str:
    """
    Formatea un perfil (semántico o procedimental) a formato TOON.
    
    Args:
        profile: Diccionario con el perfil
        
    Returns:
        String formateado en TOON
    """
    return dict_to_toon(profile)

