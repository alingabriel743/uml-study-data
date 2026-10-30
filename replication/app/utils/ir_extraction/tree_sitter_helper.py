"""
Tree-sitter parser initialization helpers.
Provides singleton parser instances for each supported language.
"""

from tree_sitter import Language, Parser
from app.utils.logger import get_logger
from tree_sitter_language_pack import get_parser

logger = get_logger(__name__)


# --- SQL ---
def get_sql_parser():
    try:
        sql_parser = get_parser("sql")
        logger.info("SQL parser initialized successfully")
        return sql_parser
    except Exception as e:
        logger.error(f"Failed to initialize SQL parser: {e}")
        raise

_SQL_PARSER = None

def get_initialized_sql_parser() -> Parser:
    global _SQL_PARSER
    if _SQL_PARSER is None:
        _SQL_PARSER = get_sql_parser()
    return _SQL_PARSER


# --- Java ---
def get_java_parser():
    try:
        java_parser = get_parser("java")
        logger.info("Java parser initialized successfully")
        return java_parser
    except Exception as e:
        logger.error(f"Failed to initialize Java parser: {e}")
        raise

_JAVA_PARSER = None

def get_initialized_java_parser() -> Parser:
    global _JAVA_PARSER
    if _JAVA_PARSER is None:
        _JAVA_PARSER = get_java_parser()
    return _JAVA_PARSER


# --- C# ---
def get_csharp_parser():
    try:
        csharp_parser = get_parser("csharp")
        logger.info("C# parser initialized successfully via language pack")
        return csharp_parser
    except Exception as e:
        logger.warning(f"Language pack failed for C#: {e}. Trying direct import...")
        try:
            import tree_sitter_c_sharp as tsc
            parser = Parser()
            parser.language(tsc.language())
            logger.info("C# parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing C# parser: {fallback_e}")
            raise

_CSHARP_PARSER = None

def get_initialized_csharp_parser() -> Parser:
    global _CSHARP_PARSER
    if _CSHARP_PARSER is None:
        _CSHARP_PARSER = get_csharp_parser()
    return _CSHARP_PARSER


# --- JavaScript ---
def get_javascript_parser():
    try:
        javascript_parser = get_parser("javascript")
        logger.info("JavaScript parser initialized successfully")
        return javascript_parser
    except Exception as e:
        logger.error(f"Failed to initialize JavaScript parser: {e}")
        raise

_JAVASCRIPT_PARSER = None

def get_initialized_javascript_parser() -> Parser:
    global _JAVASCRIPT_PARSER
    if _JAVASCRIPT_PARSER is None:
        _JAVASCRIPT_PARSER = get_javascript_parser()
    return _JAVASCRIPT_PARSER


# --- TypeScript ---
def get_typescript_parser():
    try:
        typescript_parser = get_parser("typescript")
        logger.info("TypeScript parser initialized successfully")
        return typescript_parser
    except Exception as e:
        logger.error(f"Failed to initialize TypeScript parser: {e}")
        raise

_TYPESCRIPT_PARSER = None

def get_initialized_typescript_parser() -> Parser:
    global _TYPESCRIPT_PARSER
    if _TYPESCRIPT_PARSER is None:
        _TYPESCRIPT_PARSER = get_typescript_parser()
    return _TYPESCRIPT_PARSER


# --- C++ ---
def get_cpp_parser():
    try:
        cpp_parser = get_parser("cpp")
        logger.info("C++ parser initialized successfully")
        return cpp_parser
    except Exception as e:
        logger.warning(f"Language pack failed for C++: {e}. Trying direct import...")
        try:
            import tree_sitter_cpp as tscpp
            parser = Parser()
            parser.language(tscpp.language())
            logger.info("C++ parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing C++ parser: {fallback_e}")
            raise

_CPP_PARSER = None

def get_initialized_cpp_parser() -> Parser:
    global _CPP_PARSER
    if _CPP_PARSER is None:
        _CPP_PARSER = get_cpp_parser()
    return _CPP_PARSER


# --- Kotlin ---
def get_kotlin_parser():
    try:
        kotlin_parser = get_parser("kotlin")
        logger.info("Kotlin parser initialized successfully via language pack")
        return kotlin_parser
    except Exception as e:
        logger.warning(f"Language pack failed for Kotlin: {e}. Trying direct import...")
        try:
            import tree_sitter_kotlin as tskotlin
            parser = Parser()
            parser.language(tskotlin.language())
            logger.info("Kotlin parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing Kotlin parser: {fallback_e}")
            raise

_KOTLIN_PARSER = None

def get_initialized_kotlin_parser() -> Parser:
    global _KOTLIN_PARSER
    if _KOTLIN_PARSER is None:
        _KOTLIN_PARSER = get_kotlin_parser()
    return _KOTLIN_PARSER


# --- COBOL ---
def get_cobol_parser():
    try:
        cobol_parser = get_parser("cobol")
        logger.info("COBOL parser initialized successfully via language pack")
        return cobol_parser
    except Exception as e:
        logger.warning(f"Language pack failed for COBOL: {e}. Trying direct import...")
        try:
            import tree_sitter_cobol as tscobol
            parser = Parser()
            parser.language(tscobol.language())
            logger.info("COBOL parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing COBOL parser: {fallback_e}")
            raise

_COBOL_PARSER = None

def get_initialized_cobol_parser() -> Parser:
    global _COBOL_PARSER
    if _COBOL_PARSER is None:
        _COBOL_PARSER = get_cobol_parser()
    return _COBOL_PARSER


# --- PHP ---
def get_php_parser():
    try:
        php_parser = get_parser("php")
        logger.info("PHP parser initialized successfully via language pack")
        return php_parser
    except Exception as e:
        logger.warning(f"Language pack failed for PHP: {e}. Trying direct import...")
        try:
            import tree_sitter_php as tsphp
            parser = Parser()
            parser.language(tsphp.language())
            logger.info("PHP parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing PHP parser: {fallback_e}")
            raise

_PHP_PARSER = None

def get_initialized_php_parser() -> Parser:
    global _PHP_PARSER
    if _PHP_PARSER is None:
        _PHP_PARSER = get_php_parser()
    return _PHP_PARSER


# --- Python ---
def get_python_parser():
    try:
        python_parser = get_parser("python")
        logger.info("Python parser initialized successfully via language pack")
        return python_parser
    except Exception as e:
        logger.warning(f"Language pack failed for Python: {e}. Trying direct import...")
        try:
            import tree_sitter_python as tspython
            parser = Parser()
            parser.language(tspython.language())
            logger.info("Python parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing Python parser: {fallback_e}")
            raise

_PYTHON_PARSER = None

def get_initialized_python_parser() -> Parser:
    global _PYTHON_PARSER
    if _PYTHON_PARSER is None:
        _PYTHON_PARSER = get_python_parser()
    return _PYTHON_PARSER


# --- Perl ---
def get_perl_parser():
    try:
        perl_parser = get_parser("perl")
        logger.info("Perl parser initialized successfully via language pack")
        return perl_parser
    except Exception as e:
        logger.warning(f"Language pack failed for Perl: {e}. Trying direct import...")
        try:
            import tree_sitter_perl as tsperl
            parser = Parser()
            parser.language(tsperl.language())
            logger.info("Perl parser initialized successfully via direct import")
            return parser
        except Exception as fallback_e:
            logger.error(f"Error initializing Perl parser: {fallback_e}")
            raise

_PERL_PARSER = None

def get_initialized_perl_parser() -> Parser:
    global _PERL_PARSER
    if _PERL_PARSER is None:
        _PERL_PARSER = get_perl_parser()
    return _PERL_PARSER
