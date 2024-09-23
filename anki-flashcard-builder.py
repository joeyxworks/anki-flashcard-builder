import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout
from bs4 import BeautifulSoup
import json
import time
import base64
import uuid
import hashlib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
import logging
import argparse

parser = argparse.ArgumentParser(description="A script to automatically build your Anki flashcards.")
parser.add_argument('--language', type=str, choices=['en', 'cn'], 
                    help="Select language en for English, cn for Chinese.", 
                    default='en')  # Language argument with limited choices
args = parser.parse_args()

logging.basicConfig(level=logging.INFO)

# AnkiConnect URL
ANKI_CONNECT_URL = 'http://localhost:8765'

# VocalWare credentials
API_ID = os.getenv('VW_API_ID')
ACCOUNT_ID = os.getenv('VW_ACCOUNT_ID')
SECRET_PHRASE = os.getenv('VW_SECRET_PHRASE')

if not API_ID or not ACCOUNT_ID or not SECRET_PHRASE:
    raise EnvironmentError("Missing VocalWare credentials!")

# Function to get cards from a specific deck
def get_cards(deck_name):

    payload = {
        "action": "findCards",
        "version": 6,
        "params": {
            "query": f"deck:{deck_name}"
        }
    }
    try:
        response = requests.post(ANKI_CONNECT_URL, json=payload, timeout=5)
        response.raise_for_status()
        response_data = response.json()
    except (HTTPError, ConnectionError, Timeout) as e:
        logging.error(f"Failed to fetch data from {url}: {e}")
        return None
    return response_data['result']

# Function to get note details for cards
def get_notes(cards):
    payload = {
        "action": "cardsInfo",
        "version": 6,
        "params": {
            "cards": cards
        }
    }
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    return response['result']

def add_word_info_to_note(note_id, audio_file_name, definition, examples):
    # Ensure note_id is an integer
    if not isinstance(note_id, int):
        print(f"Error: note_id must be an integer, got {type(note_id)} instead.")
        return

    # Ensure definition is a string (fallback to empty string if None)
    if definition is None:
        definition = ""
    elif not isinstance(definition, str):
        print(f"Error: definition must be a string, got {type(definition)} instead.")
        return

    # Ensure examples is a list of strings (fallback to empty string if None)
    if examples is None:
        examples_text = ""
    elif isinstance(examples, list):
        # Use <br> to insert HTML line breaks for Anki's rendering
        examples_text = "<br>".join([str(example) for example in examples])
    else:
        print(f"Error: examples must be a list, got {type(examples)} instead.")
        return

    # Debug: Check if field names are correct (especially "Extra information")
    # print(f"Updating note {note_id} with fields:")
    # print(f"Audio: [sound:{audio_file_name}]")
    # print(f"Definition: {definition}")
    # print(f"Extra information (Examples): {examples_text}")

    # Construct payload for AnkiConnect
    payload = {
        "action": "updateNoteFields",
        "version": 6,
        "params": {
            "note": {
                "id": note_id,
                "fields": {
                    "Audio": f"[sound:{audio_file_name}]",
                    "Definition": definition,
                    "Extra information": examples_text  # Correct field name with <br> for line breaks
                }
            }
        }
    }

    # Send request to AnkiConnect
    response = requests.post(ANKI_CONNECT_URL, json=payload).json()
    if response.get('error') is not None:
        logging.error(f"Error updating note {note_id}: {response['error']}")
    else:
        logging.info(f"Note {note_id} successfully updated.")
    
    # Debug: Print the full response to ensure AnkiConnect is receiving the request correctly
    # print("AnkiConnect response:", response)

# Function to get TTS audio URL from Cambridge Dictionary
def get_cambridge_word_info(word, language):
    # Replace spaces with hyphens for the search URL
    formatted_word = word.replace(' ', '-')

    language_dict = {
        'en': {'uri':'english', 'tag':'div', 'class':'def ddef_d db'},
        'cn': {'uri':'english-chinese-simplified', 'tag':'div', 'class':'tc-bb tb lpb-25 break-cj'},
    }

    if language not in language_dict:
        logging.error(f"Language '{language}' is not supported.")
        return None

    url = f"https://dictionary.cambridge.org/dictionary/{language_dict[language]['uri']}/{formatted_word}"

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}    

    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch data from Cambridge: {e}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    
    audio_url = None

    # Retrieve the audio URL
    audio_tag = soup.find('source', {'type': 'audio/mpeg'})
    
    if audio_tag and 'src' in audio_tag.attrs:
        audio_url = "https://dictionary.cambridge.org{}".format(audio_tag['src'])
    
    # Retrieve the definition
    # definition_tag = soup.find('div', {'class': 'def ddef_d db'})
    definition_tag = soup.find(language_dict[language]['tag'], {'class': language_dict[language]['class']})
    definition = definition_tag.text.strip().rstrip(':') if definition_tag else 'No definition found.'
    
    # Retrieve the examples
    examples = []
    example_tags = soup.find_all('div', {'class': 'examp dexamp'})
    if example_tags:
        # for example_tag in example_tags[:3]:  # Only keep the first three examples
        #     example_text = example_tag.text.strip()
        #     examples.append(example_text)
        examples = [example_tag.text.strip() for example_tag in example_tags[:3]]
    
    return {
        'audio_url': audio_url,
        'definition': definition,
        'examples': examples if examples else [] # Return empty list if no examples
    }

# Function to get TTS audio URL from VocalWare
def get_vocalware_tts_url(word):
    base_url = 'https://www.vocalware.com/tts/gen.php'
    params = {
        'EID': 3,  # English UK, Hugh, Adult Male
        'LID': 1,  # Language ID
        'VID': 5,  # Voice ID
        'TXT': word,
        'ACC': ACCOUNT_ID,
        'API': API_ID,
    }
    

    # Generate the hash
    hash_string = (
    str(params['EID']) +
    str(params['LID']) +
    str(params['VID']) +
    str(params['TXT']) + 
    str(params['ACC']) + 
    str(params['API']) + 
    SECRET_PHRASE  # This should NOT be URL-encoded
    )

    params['CS'] = hashlib.md5(hash_string.encode()).hexdigest()
    
    response = requests.get(base_url, params=params)
    if response.status_code == 200:
        return response.url
    return None

# Function to download audio file from URL with retries and User-Agent
def download_audio(url, file_name):
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=0.5)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    }

    try:
        response = session.get(url, headers=headers)
        response.raise_for_status()
        with open(file_name, 'wb') as file:
            file.write(response.content)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to download {url}: {e}")
        return False

# Function to upload audio file to Anki and get the correct file name
def upload_audio_to_anki(file_name):
    try:
        with open(file_name, 'rb') as file:
            data = file.read()
        b64_data = base64.b64encode(data).decode('utf-8')
        payload = {
            "action": "storeMediaFile",
            "version": 6,
            "params": {
                "filename": file_name,
                "data": b64_data
            }
        }
        response = requests.post(ANKI_CONNECT_URL, json=payload).json()
        if response.get('error') is not None:
            print(f"Error uploading file {file_name}: {response['error']}")
            return None
        return response['result']
    except Exception as e:
        print(f"Exception uploading file {file_name}: {e}")
        return None

def main(deck_name):
    cards = get_cards(deck_name)
    notes = get_notes(cards)
    
    processed_words = set()

    for note in notes:
        note_id = note['note']
        word = note['fields']['Word']['value']
        
        # Check if Audio field already has a value
        audio_field = note['fields'].get('Audio', {}).get('value', '')
        if audio_field.strip():
            # print(f"[-] Audio already exists for word: {word}, skipping...")
            logging.info(f"Audio already exists for word: {word}, skipping...")
            continue
        else:
            # print(f"[+] Download audio file for word '{word}'...")
            logging.info(f"Making cards for word '{word}'...")

        # Check if the word has already been processed
        if word in processed_words:
            # print(f"[-] Word '{word}' already processed, skipping...")
            logging.info(f"Word '{word}' already processed, skipping...")
            continue

        # Use Cambridge by default then go to VocalWare
        LANGUAGE = args.language
        cambridge_word_info = get_cambridge_word_info(word, language=LANGUAGE)
        audio_url = cambridge_word_info['audio_url']
        word_definition = cambridge_word_info['definition']
        word_examples = cambridge_word_info['examples']
        file_name = "cambridge"
        if not audio_url:
            audio_url = get_vocalware_tts_url(word)
            file_name = "vocalware"

        # audio_url = get_vocalware_tts_url(word)
        # file_name = "vocalware"
        
        if audio_url:
            # Generate a UUID for the file name
            unique_filename = f"{file_name}-{uuid.uuid4()}.mp3"
            if download_audio(audio_url, unique_filename):
                audio_file_name = upload_audio_to_anki(unique_filename)
                if audio_file_name:
                    add_word_info_to_note(note_id, audio_file_name, word_definition, word_examples)
                    processed_words.add(word)
                else:
                    print(f"[-] Failed to upload audio for word: {word}")
        else:
            print(f"[-] No audio found for word: {word}")

if __name__ == "__main__":
    main("Test")
