"""
Debug Logger Module for Wind Reseller Bot
Provides enhanced logging capabilities for troubleshooting
"""
import os
import sys
import logging
import traceback
import inspect
from datetime import datetime
import json

# Configure logging
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Create handlers for different log levels
DEBUG_LOG = os.path.join(LOG_DIR, "debug.log")
ERROR_LOG = os.path.join(LOG_DIR, "error.log")
INFO_LOG = os.path.join(LOG_DIR, "info.log")

# Setup root logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_format)

# File handlers
debug_handler = logging.FileHandler(DEBUG_LOG, encoding='utf-8')
debug_handler.setLevel(logging.DEBUG)
debug_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
debug_handler.setFormatter(debug_format)

error_handler = logging.FileHandler(ERROR_LOG, encoding='utf-8')
error_handler.setLevel(logging.ERROR)
error_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
error_handler.setFormatter(error_format)

info_handler = logging.FileHandler(INFO_LOG, encoding='utf-8')
info_handler.setLevel(logging.INFO)
info_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
info_handler.setFormatter(info_format)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(debug_handler)
logger.addHandler(error_handler)
logger.addHandler(info_handler)

def log_exception(e, context=None):
    """
    Log an exception with detailed information including stack trace, 
    function call location, and context.
    
    Args:
        e: The exception to log
        context: Additional context about where the exception occurred
    """
    exc_type, exc_value, exc_traceback = sys.exc_info()
    stack_trace = traceback.format_exception(exc_type, exc_value, exc_traceback)
    
    # Get caller information
    frame = inspect.currentframe().f_back
    file_name = frame.f_code.co_filename
    line_number = frame.f_lineno
    function_name = frame.f_code.co_name
    
    # Format error message
    error_message = f"""
EXCEPTION OCCURRED:
Time: {datetime.now().isoformat()}
Function: {function_name}
Location: {file_name}:{line_number}
Type: {exc_type.__name__}
Message: {str(e)}
Context: {json.dumps(context) if context else "None"}
Stack Trace:
{''.join(stack_trace)}
--------------------------------------------------
"""
    
    # Log to error log
    logger.error(error_message)
    return error_message

def log_function_call(func):
    """
    Decorator to log function calls, arguments, and return values
    """
    def wrapper(*args, **kwargs):
        # Format args and kwargs for logging
        args_str = ', '.join([str(arg) for arg in args])
        kwargs_str = ', '.join([f"{k}={v}" for k, v in kwargs.items()])
        
        # Log the function call
        logger.debug(f"CALL: {func.__name__}({args_str}{', ' if args_str and kwargs_str else ''}{kwargs_str})")
        
        try:
            # Call the original function
            result = func(*args, **kwargs)
            
            # Log the successful return
            if not inspect.iscoroutinefunction(func):
                # Only log result for non-async functions
                logger.debug(f"RETURN: {func.__name__} -> {str(result)[:100]}")
            
            return result
        except Exception as e:
            # Log the exception
            log_exception(e, {
                "function": func.__name__,
                "args": str(args),
                "kwargs": str(kwargs)
            })
            
            # Re-raise the exception
            raise
    
    # For async functions, we need a different approach
    if inspect.iscoroutinefunction(func):
        async def async_wrapper(*args, **kwargs):
            # Format args and kwargs for logging
            args_str = ', '.join([str(arg) for arg in args])
            kwargs_str = ', '.join([f"{k}={v}" for k, v in kwargs.items()])
            
            # Log the function call
            logger.debug(f"ASYNC CALL: {func.__name__}({args_str}{', ' if args_str and kwargs_str else ''}{kwargs_str})")
            
            try:
                # Call the original function
                result = await func(*args, **kwargs)
                
                # Don't log result for async functions as they might be complex
                logger.debug(f"ASYNC RETURN: {func.__name__} completed")
                
                return result
            except Exception as e:
                # Log the exception
                log_exception(e, {
                    "function": func.__name__,
                    "args": str(args),
                    "kwargs": str(kwargs)
                })
                
                # Re-raise the exception
                raise
        
        return async_wrapper
    
    return wrapper

def log_telegram_update(update):
    """
    Log Telegram update object (messages, callbacks, etc.)
    """
    # Extract key information from the update
    update_id = update.update_id
    
    # Handle different types of updates
    if update.message:
        user_id = update.message.from_user.id
        username = update.message.from_user.username
        chat_id = update.message.chat_id
        message_type = "text" if update.message.text else "photo" if update.message.photo else "document" if update.message.document else "other"
        content = update.message.text if update.message.text else f"[{message_type}]"
        
        logger.info(f"TELEGRAM UPDATE: update_id={update_id}, user_id={user_id}, username={username}, chat_id={chat_id}, type={message_type}, content={content[:50]}")
    
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
        username = update.callback_query.from_user.username
        chat_id = update.callback_query.message.chat_id if update.callback_query.message else "None"
        data = update.callback_query.data
        
        logger.info(f"TELEGRAM CALLBACK: update_id={update_id}, user_id={user_id}, username={username}, chat_id={chat_id}, data={data}")
    
    elif update.inline_query:
        user_id = update.inline_query.from_user.id
        username = update.inline_query.from_user.username
        query = update.inline_query.query
        
        logger.info(f"TELEGRAM INLINE: update_id={update_id}, user_id={user_id}, username={username}, query={query}")
    
    else:
        # Handle other types of updates
        logger.info(f"TELEGRAM OTHER: update_id={update_id}, data={str(update)[:100]}")
