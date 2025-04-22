import re
import asyncio
from collections import defaultdict
from itertools import product
import time
import unicodedata
from typing import List, Dict, Set, Optional
from functools import lru_cache
from langdetect import detect, LangDetectException
import nltk
from nltk.corpus import wordnet

# ==================== CONSTANTS ====================
HIDDEN_SEPARATORS = [
    '\u200B', '\u200C', '\u200D', '\u2060',  # Zero-width spaces
    '\u034F', '\u180E', '\uFEFF',            # Other hidden chars
    '\u00AD',                                # Soft hyphen
    '\u17B5', '\u17B6',                      # Khmer vowel signs
    '\u2028', '\u2029',                      # Line/paragraph separators
    '\u1160', '\u3164',                      # Hangul filler
]
# Comprehensive character substitution dictionary for text filtering
COMBINED_SUBSTITUTIONS = {
    # Lowercase letters
    'a': ['a', '@', '4', 'α', 'λ', '*', 'ⓐ', 'Ⓐ', 'ａ', 'Ａ', 'à', 'á', 'â', 'ã', 'ä', 'å', 'ᴀ', 'ɐ', 'ɒ', 'Д', 'Å', '𝐚', '𝒂', '𝒶', '𝓪', '𝔞', '𝕒', '𝖆', '𝖺', '𝗮', '𝘢', '𝙖', '𝚊', 'ₐ', 'ᵃ', 'ᵄ', '🄰', '🅐', '🅰', '🅚', '🅫', '🅰︎', '🄰︎'],
    'b': ['b', '8', '6', 'β', '*', 'ⓑ', 'Ⓑ', 'ｂ', 'Ｂ', 'ḃ', 'ḅ', 'ḇ', 'ʙ', 'ɓ', 'Ь', 'ß', '𝐛', '𝒃', '𝒷', '𝓫', '𝔟', '𝕓', '𝖇', '𝖻', '𝗯', '𝘣', '𝙗', '𝚋', 'ᵇ', '🄱', '🅑', '🅱', '🅛', '🅬', '🅱︎', '🄱︎'],
    'c': ['c', '(', '<', 'ç', '*', 'ⓒ', 'Ⓒ', 'ｃ', 'Ｃ', 'ć', 'ĉ', 'ċ', 'č', 'ᴄ', 'ɔ', '¢', '𝐜', '𝒄', '𝒸', '𝓬', '𝔠', '𝕔', '𝖈', '𝖼', '𝗰', '𝘤', '𝙘', '𝚌', 'ᶜ', '🄲', '🅒', '🅲', '🅜', '🅭', '🅲︎', '🄲︎'],
    'd': ['d', '|)', 'ⓓ', 'Ⓓ', 'ｄ', 'Ｄ', 'ď', 'đ', 'ᴅ', 'ɖ', 'ð', '𝐝', '𝒅', '𝒹', '𝓭', '𝔡', '𝕕', '𝖉', '𝖽', '𝗱', '𝘥', '𝙙', '𝚍', 'ᵈ', '🄳', '🅓', '🅳', '🅝', '🅮', '🅳︎', '🄳︎'],
    'e': ['e', '3', '€', 'ε', '*', 'ⓔ', 'Ⓔ', 'ｅ', 'Ｅ', 'è', 'é', 'ê', 'ë', 'ē', 'ĕ', 'ė', 'ę', 'ě', 'ᴇ', 'ɘ', '£', '𝐞', '𝒆', 'ℯ', '𝓮', '𝔢', '𝕖', '𝖊', '𝖾', '𝗲', '𝘦', '𝙚', '𝚎', 'ₑ', 'ᵉ', '🄴', '🅔', '🅴', '🅞', '🅯', '🅴︎', '🄴︎'],
    'f': ['f', 'ƒ', 'ⓕ', 'Ⓕ', 'ｆ', 'Ｆ', 'ꜰ', 'ʄ', 'ʃ', '𝐟', '𝒇', '𝒻', '𝓯', '𝔣', '𝕗', '𝖋', '𝖿', '𝗳', '𝘧', '𝙛', '𝚏', 'ᶠ', '🄵', '🅕', '🅵', '🅟', '🅰', '🅵︎', '🄵︎'],
    'g': ['g', '9', 'ⓖ', 'Ⓖ', 'ｇ', 'Ｇ', 'ĝ', 'ğ', 'ġ', 'ģ', 'ɢ', 'ɡ', '𝐠', '𝒈', 'ℊ', '𝓰', '𝔤', '𝕘', '𝖌', '𝗀', '𝗴', '𝘨', '𝙜', '𝚐', 'ᵍ', '🄶', '🅖', '🅶', '🅠', '🅱', '🅶︎', '🄶︎'],
    'h': ['h', '#', 'ⓗ', 'Ⓗ', 'ｈ', 'Ｈ', 'ĥ', 'ħ', 'ʜ', 'ɦ', 'н', '𝐡', '𝒉', '𝒽', '𝓱', '𝔥', '𝕙', '𝖍', '𝗁', '𝗵', '𝘩', '𝙝', '𝚑', 'ₕ', 'ʰ', '🄷', '🅗', '🅷', '🅡', '🅲', '🅷︎', '🄷︎'],
    'i': ['i', '1', '!', '|', 'ι', '*', 'ⓘ', 'Ⓘ', 'ｉ', 'Ｉ', 'ì', 'í', 'î', 'ï', 'ĩ', 'ī', 'ĭ', 'į', 'ı', 'ɪ', 'ɨ', '¡', '𝐢', '𝒊', '𝒾', '𝓲', '𝔦', '𝕚', '𝖎', '𝗂', '𝗶', '𝘪', '𝙞', '𝚒', 'ᵢ', 'ⁱ', '🄸', '🅘', '🅸', '🅢', '🅳', '🅸︎', '🄸︎'],
    'j': ['j', 'ⓙ', 'Ⓙ', 'ｊ', 'Ｊ', 'ĵ', 'ᴊ', 'ʝ', 'ل', '𝐣', '𝒋', '𝒿', '𝓳', '𝔧', '𝕛', '𝖏', '𝗃', '𝗷', '𝘫', '𝙟', '𝚓', 'ʲ', '🄹', '🅙', '🅹', '🅣', '🅴', '🅹︎', '🄹︎'],
    'k': ['k', 'ⓚ', 'Ⓚ', 'ｋ', 'Ｋ', 'ķ', 'ᴋ', 'ʞ', 'κ', '𝐤', '𝒌', '𝓀', '𝓴', '𝔨', '𝕜', '𝖐', '𝗄', '𝗸', '𝘬', '𝙠', '𝚔', 'ₖ', 'ᵏ', '🄺', '🅚', '🅺', '🅤', '🅵', '🅺︎', '🄺︎'],
    'l': ['l', '|', '*', 'ⓛ', 'Ⓛ', 'ｌ', 'Ｌ', 'ĺ', 'ļ', 'ľ', 'ŀ', 'ł', 'ʟ', 'ɭ', '£', '𝐥', '𝒍', '𝓁', '𝓵', '𝔩', '𝕝', '𝖑', '𝗅', '𝗹', '𝘭', '𝙡', '𝚕', 'ₗ', 'ˡ', '🄻', '🅛', '🅻', '🅥', '🅶', '🅻︎', '🄻︎', '1', 'I', 'i'],
    'm': ['m', 'ⓜ', 'Ⓜ', 'ｍ', 'Ｍ', 'ᴍ', 'ɱ', 'м', '𝐦', '𝒎', '𝓂', '𝓶', '𝔪', '𝕞', '𝖒', '𝗆', '𝗺', '𝘮', '𝙢', '𝚖', 'ᵐ', '🄼', '🅜', '🅼', '🅦', '🅷', '🅼︎', '🄼︎'],
    'n': ['n', 'ⓝ', 'Ⓝ', 'ｎ', 'Ｎ', 'ñ', 'ń', 'ņ', 'ň', 'ŉ', 'ɴ', 'ɲ', 'и', '𝐧', '𝒏', '𝓃', '𝓷', '𝔫', '𝕟', '𝖓', '𝗇', '𝗻', '𝘯', '𝙣', '𝚗', 'ₙ', 'ⁿ', '🄽', '🅝', '🅽', '🅧', '🅸', '🅽︎', '🄽︎'],
    'o': ['o', '0', '()', 'ο', '*', 'ⓞ', 'Ⓞ', 'ｏ', 'Ｏ', 'ò', 'ó', 'ô', 'õ', 'ö', 'ø', 'ō', 'ŏ', 'ő', 'ᴏ', 'ɵ', 'θ', '𝐨', '𝒐', 'ℴ', '𝓸', '𝔬', '𝕠', '𝖔', '𝗈', '𝗼', '𝘰', '𝙤', '𝚘', 'ₒ', 'ᵒ', '🄾', '🅞', '🅾', '🅨', '🅹', '🅾︎', '🄾︎'],
    'p': ['p', 'ⓟ', 'Ⓟ', 'ｐ', 'Ｐ', 'ᴘ', 'ƥ', 'ρ', '𝐩', '𝒑', '𝓹', '𝔭', '𝕡', '𝖕', '𝗉', '𝗽', '𝘱', '𝙥', '𝚙', 'ᵖ', '🄿', '🅟', '🅿', '🅩', '🅺', '🅿︎', '🄿︎'],
    'q': ['q', 'ⓠ', 'Ⓠ', 'ｑ', 'Ｑ', 'ϙ', 'ʠ', '𝐪', '𝒒', '𝓆', '𝓺', '𝔮', '𝕢', '𝖖', '𝗊', '𝗾', '𝘲', '𝙦', '𝚚', '🅀', '🅠', '🆀', '🅻', '🆀︎', '🅀︎'],
    'r': ['r', 'ⓡ', 'Ⓡ', 'ｒ', 'Ｒ', 'ŕ', 'ŗ', 'ř', 'ʀ', 'ɹ', 'я', '𝐫', '𝒓', '𝓇', '𝓻', '𝔯', '𝕣', '𝖗', '𝗋', '𝗿', '𝘳', '𝙧', '𝚛', 'ᵣ', 'ʳ', '🅁', '🅡', '🆁', '🅼', '🆁︎', '🅁︎'],
    's': ['s', '5', '$', '*', 'ⓢ', 'Ⓢ', 'ｓ', 'Ｓ', 'ś', 'ŝ', 'ş', 'š', 'ſ', 'ꜱ', 'ʂ', '§', '𝐬', '𝒔', '𝓈', '𝓼', '𝔰', '𝕤', '𝖘', '𝗌', '𝘀', '𝘴', '𝙨', '𝚜', 'ₛ', 'ˢ', '🅂', '🅢', '🆂', '🅽', '🆂︎', '🅂︎'],
    't': ['t', '7', '+', 'τ', '*', 'ⓣ', 'Ⓣ', 'ｔ', 'Ｔ', 'ţ', 'ť', 'ŧ', 'ᴛ', 'ʈ', '†', '𝐭', '𝒕', '𝓉', '𝓽', '𝔱', '𝕥', '𝖙', '𝗍', '𝘁', '𝘵', '𝙩', '𝚝', 'ₜ', 'ᵗ', '🅃', '🅣', '🆃', '🅾', '🆃︎', '🅃︎'],
    'u': ['u','@', 'v', 'υ', '*', 'ⓤ', 'Ⓤ', 'ｕ', 'Ｕ', 'ù', 'ú', 'û', 'ü', 'ũ', 'ū', 'ŭ', 'ů', 'ű', 'ų', 'ᴜ', 'ʊ', 'µ', '𝐮', '𝒖', '𝓊', '𝓾', '𝔲', '𝕦', '𝖚', '𝗎', '𝘂', '𝘶', '𝙪', '𝚞', 'ᵤ', 'ᵘ', '🅄', '🅤', '🆄', '🅿', '🆄︎', '🅄︎'],
    'v': ['v', 'u', '*', 'ⓥ', 'Ⓥ', 'ｖ', 'Ｖ', 'ᴠ', 'ʋ', 'ѵ', '𝐯', '𝒗',
'𝓋', '𝓿', '𝔳', '𝕧', '𝖛', '𝗏', '𝘃', '𝘷', '𝙫', '𝚟', 'ᵛ', '🅅', '🅥', '🆅', '🆅︎', '🅅︎'],
    'w': ['w', 'ⓦ', 'Ⓦ', 'ｗ', 'Ｗ', 'ᴡ', 'ʍ', 'ω', '𝐰', '𝒘', '𝓌', '𝔀', '𝔴', '𝕨', '𝖜', '𝗐', '𝘄', '𝘸', '𝙬', '𝚠', 'ʷ', '🅆', '🅦', '🆆', '🆆︎', '🅆︎', 'vv', 'uu'],
    'x': ['x', '×', '*', 'ⓧ', 'Ⓧ', 'ｘ', 'Ｘ', '᙮', 'χ', 'ж', '𝐱', '𝒙', '𝓍', '𝔁', '𝔵', '𝕩', '𝖝', '𝗑', '𝘅', '𝘹', '𝙭', '𝚡', 'ˣ', '🅇', '🅧', '🆇', '🆇︎', '🅇︎', '><'],
    'y': ['y', 'ⓨ', 'Ⓨ', 'ｙ', 'Ｙ', 'ý', 'ÿ', 'ŷ', 'ʏ', 'ɣ', 'у', '𝐲', '𝒚', '𝓎', '𝔂', '𝔶', '𝕪', '𝖞', '𝗒', '𝘆', '𝘺', '𝙮', '𝚢', 'ʸ', '🅈', '🅨', '🆈', '🆈︎', '🅈︎'],
    'z': ['z', '2', '*', 'ⓩ', 'Ⓩ', 'ｚ', 'Ｚ', 'ź', 'ż', 'ž', 'ᴢ', 'ʐ', 'з', '𝐳', '𝒛', '𝓏', '𝔃', '𝔷', '𝕫', '𝖟', '𝗓', '𝘇', '𝘻', '𝙯', '𝚣', 'ᶻ', '🅉', '🅩', '🆉', '🆉︎', '🅉︎'],
    
    # Uppercase letters 
    'A': ['A', '𝐀', '𝑨', '𝒜', '𝓐', '𝔄', '𝔸', '𝕬', '𝖠', '𝗔', '𝘈', '𝘼', '𝙰', '🄐', '🅐', '🅰', '4', '@'],
    'B': ['B', '𝐁', '𝑩', 'ℬ', '𝓑', '𝔅', '𝔹', '𝕭', '𝖡', '𝗕', '𝘉', '𝘽', '𝙱', '🄑', '🅑', '🅱', '8', '6'],
    'C': ['C', '𝐂', '𝑪', '𝒞', '𝓒', 'ℭ', 'ℂ', '𝕮', '𝖢', '𝗖', '𝘊', '𝘾', '𝙲', '🄒', '🅒', '🅲', '(', '<'],
    'D': ['D', '𝐃', '𝑫', '𝒟', '𝓓', '𝔇', '𝔻', '𝕯', '𝖣', '𝗗', '𝘋', '𝘿', '𝙳', '🄓', '🅓', '🅳'],
    'E': ['E', '𝐄', '𝑬', 'ℰ', '𝓔', '𝔈', '𝔼', '𝕰', '𝖤', '𝗘', '𝘌', '𝙀', '𝙴', '🄔', '🅔 ','🅴', '3', '€'],
    'F': ['F', '𝐅', '𝑭', 'ℱ', '𝓕', '𝔉', '𝔽', '𝕱', '𝖥', '𝗙', '𝘍', '𝙁', '𝙵', '🄕', '🅕', '🅵'],
    'G': ['G', '𝐆', '𝑮', '𝒢', '𝓖', '𝔊', '𝔾', '𝕲', '𝖦', '𝗚', '𝘎', '𝙂', '𝙶', '🄖', '🅖', '🅶', '9'],
    'H': ['H', '𝐇', '𝑯', 'ℋ', '𝓗', 'ℌ', 'ℍ', '𝕳', '𝖧', '𝗛', '𝘏', '𝙃', '𝙷', '🄗', '🅗', '🅷', '#'],
    'I': ['I', '𝐈', '𝑰', 'ℐ', '𝓘', 'ℑ', '𝕀', '𝕴', '𝖨', '𝗜', '𝘐', '𝙄', '𝙸', '🄘', '🅘', '🅸', '1', '!', '|', 'l', 'i'],
    'J': ['J', '𝐉', '𝑱', '𝒥', '𝓙', '𝔍', '𝕁', '𝕵', '𝖩', '𝗝', '𝘑', '𝙅', '𝙹', '🄙', '🅙', '🅹'],
    'K': ['K', '𝐊', '𝑲', '𝒦', '𝓚', '𝔎', '𝕂', '𝕶', '𝖪', '𝗞', '𝘒', '𝙆', '𝙺', '🄚', '🅚', '🅺'],
    'L': ['L', '𝐋', '𝑳', 'ℒ', '𝓛', '𝔏', '𝕃', '𝕷', '𝖫', '𝗟', '𝘓', '𝙇', '𝙻', '🄛', '🅛', '🅻', '1', 'I', 'i', '|'],
    'M': ['M', '𝐌', '𝑴', 'ℳ', '𝓜', '𝔐', '𝕄', '𝕸', '𝖬', '𝗠', '𝘔', '𝙈', '𝙼', '🄜', '🅜', '🅼'],
    'N': ['N', '𝐍', '𝑵', '𝒩', '𝓝', '𝔑', 'ℕ', '𝕹', '𝖭', '𝗡', '𝘕', '𝙉', '𝙽', '🄝', '🅝', '🅽'],
    'O': ['O', '𝐎', '𝑶', '𝒪', '𝓞', '𝔒', '𝕆', '𝕺', '𝖮', '𝗢', '𝘖', '𝙊', '𝙾', '🄞', '🅞', '🅾', '0', '()'],
    'P': ['P', '𝐏', '𝑷', '𝒫', '𝓟', '𝔓', 'ℙ', '𝕻', '𝖯', '𝗣', '𝘗', '𝙋', '𝙿', '🄟', '🅟', '🆀'],
    'Q': ['Q', '𝐐', '𝑸', '𝒬', '𝓠', '𝔔', 'ℚ', '𝕼', '𝖰', '𝗤', '𝘘', '𝙌', '𝚀', '🄠', '🅠', '🆁'],
    'R': ['R', '𝐑', '𝑹', 'ℛ', '𝓡', 'ℜ', 'ℝ', '𝕽', '𝖱', '𝗥', '𝘙', '𝙍', '𝚁', '🄡', '🅡', '🆂'],
    'S': ['S', '𝐒', '𝑺', '𝒮', '𝓢', '𝔖', '𝕊', '𝕾', '𝖲', '𝗦', '𝘚', '𝙎', '𝚂', '🄢', '🅢', '🆃', '5', '$'],
    'T': ['T', '𝐓', '𝑻', '𝒯', '𝓣', '𝔗', '𝕋', '𝕿', '𝖳', '𝗧', '𝘛', '𝙏', '𝚃', '🄣', '🅣', '🆄', '7', '+'],
    'U': ['U', '𝐔', '𝑼', '𝒰', '𝓤', '𝔘', '𝕌', '𝖀', '𝖴', '𝗨', '𝘜', '𝙐', '𝚄', '🄤', '🅤', '🆄', 'V'],
    'V': ['V', '𝐕', '𝑽', '𝒱', '𝓥', '𝔙', '𝕍', '𝖁', '𝖵', '𝗩', '𝘝', '𝙑', '𝚅', '🄥', '🅥', '🆅', 'U'],
    'W': ['W', '𝐖', '𝑾', '𝒲', '𝓦', '𝔚', '𝕎', '𝖂', '𝖶', '𝗪', '𝘞', '𝙒', '𝚆', '🄦', '🅦', '🆇', 'VV', 'UU'],
    'X': ['X', '𝐗', '𝑿', '𝒳', '𝓧', '𝔛', '𝕏', '𝖃', '𝖷', '𝗫', '𝘟', '𝙓', '𝚇', '🄧', '🅧', '🆈', '><'],
    'Y': ['Y', '𝐘', '𝒀', '𝒴', '𝓨', '𝔜', '𝕐', '𝖄', '𝖸', '𝗬', '𝘠', '𝙔', '𝚈', '🄨', '🅨', '🆉'],
    'Z': ['Z', '𝐙', '𝒁', '𝒵', '𝓩', 'ℨ', 'ℤ', '𝖅', '𝖹', '𝗭', '𝘡', '𝙕', '𝚉', '🄩', '🅩', '🆊'],

    '0': ['0', '⓪', '０', '𝟎', '𝟘', '𝟢', '𝟬', '𝟶', 'o', 'O', '()', 'ο', '*', 'ⓞ', 'Ⓞ', 'ｏ', 'Ｏ', 'ò', 'ó', 'ô', 'õ', 'ö', 'ø', 'ō', 'ŏ', 'ő', 'ᴏ', 'ɵ', 'θ'],
    '1': ['1', '①', '１', '𝟏', '𝟙', '𝟣', '𝟭', '𝟷', 'I', 'i', '!', '|', 'ι', '*', 'ⓘ', 'Ⓘ', 'ｉ', 'Ｉ', 'ì', 'í', 'î', 'ï', 'ĩ', 'ī', 'ĭ', 'į', 'ı', 'ɪ', 'ɨ', '¡'],
    '2': ['2', '②', '２', '𝟐', '𝟚', '𝟤', '𝟮', '𝟸', 'z', 'Z', '*', 'ⓩ', 'Ⓩ', 'ｚ', 'Ｚ', 'ź', 'ż', 'ž', 'ᴢ', 'ʐ', 'з'],
    '3': ['3', '③', '３', '𝟑', '𝟛', '𝟥', '𝟯', '𝟹', 'e', 'E', '€', 'ε', '*', 'ⓔ', 'Ⓔ', 'ｅ', 'Ｅ', 'è', 'é', 'ê', 'ë', 'ē', 'ĕ', 'ė', 'ę', 'ě', 'ᴇ', 'ɘ', '£'],
    '4': ['4', '④', '４', '𝟒', '𝟜', '𝟦', '𝟰', '𝟺', 'a', 'A', '@', 'α', 'λ', '*', 'ⓐ', 'Ⓐ', 'ａ', 'Ａ', 'à', 'á', 'â', 'ã', 'ä', 'å', 'ᴀ', 'ɐ', 'ɒ', 'Д', 'Å', 'h'],
    '5': ['5', '⑤', '５', '𝟓', '𝟝', '𝟧', '𝟱', '𝟻', 's', 'S', '*', 'ⓢ', 'Ⓢ', 'ｓ', 'Ｓ', 'ś', 'ŝ', 'ş', 'š', 'ſ', 'ꜱ', 'ʂ', '§'],
    '6': ['6', '⑥', '６', '𝟔', '𝟞', '𝟨', '𝟲', '𝟼', 'b', 'B', '8', 'β', '*', 'ⓑ', 'Ⓑ', 'ｂ', 'Ｂ', 'ḃ', 'ḅ', 'ḇ', 'ʙ', 'ɓ', 'Ь', 'ß'],
    '7': ['7', '⑦', '７', '𝟕', '𝟟', '𝟩', '𝟳', '𝟽', 't', 'T', '+', 'τ', '*', 'ⓣ', 'Ⓣ', 'ｔ', 'Ｔ', 'ţ', 'ť', 'ŧ', 'ᴛ', 'ʈ', '†'],
    '8': ['8', '⑧', '８', '𝟖', '𝟠', '𝟪', '𝟴', '𝟾', 'b', 'B', '6', 'β', '*', 'ⓑ', 'Ⓑ', 'ｂ', 'Ｂ', 'ḃ', 'ḅ', 'ḇ', 'ʙ', 'ɓ', 'Ь', 'ß'],
    '9': ['9', '⑨', '９', '𝟗', '𝟡', '𝟫', '𝟵', '𝟿', 'g', 'G', 'ⓖ', 'Ⓖ', 'ｇ', 'Ｇ', 'ĝ', 'ğ', 'ġ', 'ģ', 'ɢ', 'ɡ']
}

for base, variants in COMBINED_SUBSTITUTIONS.items():
    COMBINED_SUBSTITUTIONS[base] = sorted(set(variants), key=lambda x: (len(x), x))

CLEAN_BRACKETS = {"[", "]", "{", "}", "(", ")"}

# === HOMOGLYPHS
HOMOGLYPHS = {
    'ѕ': 's', 'с': 'c', 'е': 'e', 'а': 'a', 'р': 'p', 'о': 'o', 'і': 'i',
    'ԁ': 'd', 'ӏ': 'l', 'һ': 'h', 'ԛ': 'q', 'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c',
    'ᴅ': 'd', 'ᴇ': 'e', 'ғ': 'f', 'ɢ': 'g', 'н': 'h', 'ɪ': 'i', 'ᴊ': 'j',
    'ᴋ': 'k', 'ʟ': 'l', 'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o', 'ᴘ': 'p', 'ǫ': 'q',
    'ʀ': 'r', 'ѕ': 's', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'ʏ': 'y', 'ᴢ': 'z'
}

# === Build Reverse Map
REVERSE_SUBSTITUTIONS = defaultdict(set)
for base, variants in COMBINED_SUBSTITUTIONS.items():
    for v in variants:
        for form in {v, v.lower(), v.upper()}:
            REVERSE_SUBSTITUTIONS[form].add(base.lower())

# === Text Preprocessing
def squash_repeats(text: str, threshold: int = 2) -> str:
    return re.sub(r'(.)\1{' + str(threshold - 1) + r',}', r'\1', text)

def normalize_homoglyphs(text: str) -> str:
    return ''.join(HOMOGLYPHS.get(c, c) for c in text)

def strip_nonalpha_punct(text: str) -> str:
    return re.sub(r'[^a-zA-Z0-9\s]', '', text)

def collapse_spaced_letters(text: str) -> str:
    return re.sub(r'(?i)\b(?:[a-z]\s+){2,}[a-z]\b', lambda m: m.group(0).replace(' ', ''), text)

def preprocess_text_for_filtering(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = normalize_homoglyphs(text)
    text = squash_repeats(text)
    text = collapse_spaced_letters(text)
    text = strip_nonalpha_punct(text)
    return text.lower().strip()
# ==================== Normalization + Preprocessing ====================

def build_normalization_map(substitutions: Dict[str, List[str]]) -> Dict[str, str]:
    """Create a consistent normalization map from all variants to their base characters."""
    norm_map = {}
    for base, variants in substitutions.items():
        for var in variants:
            for form in {var, var.lower(), var.upper()}:
                if form not in norm_map:
                    norm_map[form] = base.lower()
    return norm_map

NORMALIZATION_MAP = build_normalization_map(COMBINED_SUBSTITUTIONS)

def expand_all_normalizations(word: str, max_variants: int = 50000) -> set:
    possibilities = []
    for char in word:
        options = list(REVERSE_SUBSTITUTIONS.get(char, {char}))
        possibilities.append(sorted(set(options), key=lambda x: (len(x), x)))

    all_combos = set()
    for combo in product(*possibilities):
        all_combos.add(''.join(combo))
        if len(all_combos) >= max_variants:
            break
    return all_combos

def remove_hidden_chars(text: str) -> str:
    """Remove invisible characters that can be used to bypass filters."""
    for char in HIDDEN_SEPARATORS:
        text = text.replace(char, '')
    return text

def normalize_to_base(text: str) -> str:
    """Replace obfuscated variants using regex — supports symbols & multichars."""
    sorted_variants = sorted(NORMALIZATION_MAP.items(), key=lambda x: -len(x[0]))
    for variant, base in sorted_variants:
        try:
            text = re.sub(re.escape(variant), base, text, flags=re.IGNORECASE)
        except Exception as e:
            print(f"Sub error: {variant} -> {base} = {e}")
    return text

def squeeze_text(text: str) -> str:
    """Remove all non-alphanumeric characters (like spaces, dots, dashes, etc)."""
    return re.sub(r'[^a-zA-Z0-9]', '', text)


CONTEXT_WHITELIST = {
    'cunt': {
        'patterns': [
            r'\bcunt(?:ry|ries|ing|ed|ious|ure|ship)\b',
            r'\b(?:dis|re)cunt\b',
            r'\bcount\b',
            r'\baccount\b'
        ],
        'timeout': 1.0},
    'ass': {
        'patterns': [
            r'\bass\s*(?:ignment|essment|ociation|embly|ets|ist|uming|ert)',
            r'\b(?:cl|gr|m|p)ass\b',
            r'\b(?:embr|harr)ass'
        ],
        'timeout': 1.0
    },
    'cock': {
        'patterns': [
            r'\bcock(?:tail|atoo|pit|roach)',
            r'\bpea(?:cock)\b',
            r'\bhancock\b',
            r'\bshuttle(?:cock)\b'
        ],
        'timeout': 1.0
    },
    'hell': {
        'patterns': [
            r'\bhell(?:o|icopter|met|ium|enic)',
            r'\bshell\b',
            r'\bothello\b'
        ],
        'timeout': 1.0
    }
}

SUFFIX_RULES = {
    'ing': {'min_length': 4, 'exceptions': set(['ring', 'king', 'sing'])},
    'er': {'min_length': 3, 'exceptions': set(['her', 'per'])},
    'ed': {'min_length': 3, 'exceptions': set(['red', 'bed'])},
    'a': {'min_length': 4, 'exceptions': set(['banana'])} , # New rule for cases like 'fucka'
    's': {'min_length': 3, 'exceptions': set(['is', 'as', 'us'])},
    'es': {'min_length': 4, 'exceptions': set(['yes', 'res', 'des'])}
}

COMMON_SAFE_WORDS = {
    "penistone", "lightwater", "cockburn", "mianus",
    "hello","tatsuki", "cumming", "clitheroe", "twatt", "fanny", "assington", "bitchfield",
    "titcomb", "rape", "shitterton", "prickwillow", "whale", "beaver",
    "cocktail", "passage", "classic", "grassland", "bassist", "butterfly",
    "shipment", "shooting", "language", "counting", "cluster", "glassware",
    "testes", "scrotum", "vaginal", "urethra", "mastectomy", "vasectomy",
    "nucleus", "molecular", "pascal", "vascular", "fascial",
    "clitheroe", "twatt", "fanny", "assington", "bitchfield", "titcomb",
    "shitterton", "prickwillow", "cockermouth", "cockbridge"
}

SHORT_SWEARS = {
    'fx', 'fk', 'sht', 'wtf', 'ffs', 'ngr', 'bch', 'cnt', 'dck',
    'fck', 'sh1', '5ht', 'vgn', 'prn', 'f4n', 'n1g', 'k3k', 'fku',
    'sht', 'ass', 'bch', 'cnt', 'dck', 'fuk', 'fuc', 'sh1', 'fgs',
    'ngr', 'wth', 'dmn', 'bch', 'cnt', 'dmn', 'fgs', 'ngr', 'prk',
    'twt', 'vgn', 'prn', 'f4n', 'n1g', 'k3k', 'fku', 'sht'
}

# ==================== UTILITY FUNCTIONS ====================


def simple_metaphone(s: str, max_length: int = 8) -> str:
    """Improved phonetic algorithm to catch misspellings like 'fukc'"""
    if not s:
        return ""
    
    s = s.lower()
    # First normalize to base characters
    s = normalize_to_base(s)
    
    replacements = [
        (r'[^a-z]', ''),          # Remove non-letters
        (r'([aeiou])h', r'\1'),    # vowel+h → vowel
        (r'gh(?=[iey])', ''),      # silent gh
        (r'ck', 'k'),              # ck → k
        (r'c(?!e|i|y)', 'k'),      # Hard c → k
        (r'ph', 'f'),              # ph → f
        (r'qu', 'kw'),             # qu → kw
        (r'x', 'ks'),              # x → ks
        (r'(\w)\1+', r'\1'),       # Remove duplicates
        (r'sch', 'sk'),            # sch → sk
        (r'th', 't'),              # th → t
        (r'^kn', 'n'),             # silent k
        (r'^gn', 'n'),             # silent g
        (r'^pn', 'n'),             # silent p
        (r'^wr', 'r'),             # silent w
        (r'mb$', 'm'),             # silent b
        # Additional rules for common misspellings
        (r'([^s]|^)c(?=[iey])', r'\1s'),  # c→s before e,i,y (except after s)
        (r'([^f]|^)gh', r'\1g'),   # gh→g (except after f)
        (r'([^t]|^)ch', r'\1k'),   # ch→k (except after t)
    ]
    
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s)
    
    # Sort consonants to catch transpositions
    if len(s) >= 4:
        first = s[0]
        last = s[-1]
        middle = ''.join(sorted(s[1:-1])) if len(s) > 2 else ''
        s = first + middle + last
    
    return s[:max_length]

def split_words(input_text: str) -> List[str]:
    """Split input into words (handles both comma and space separated words)
    
    Examples:
        "word1 word2 word3" → ["word1", "word2", "word3"]
        "word1,word2,word3" → ["word1", "word2", "word3"]
        "word1, word2, word3" → ["word1", "word2", "word3"]
        "word1 word2,word3" → ["word1", "word2", "word3"]
    """
    text = input_text.lower()
    # Remove all special characters except letters, numbers, commas and spaces
    text = re.sub(r"[^a-z0-9,\s]", "", text)
    
    # Split by both commas and whitespace, then clean each word
    words = []
    for word in re.split(r"[, \s]+", text):
        word = word.strip()
        if word:
            words.append(word)
    
    # Remove duplicates while preserving order
    return list(dict.fromkeys(words))

import os

def load_safe_words(swear_words: set = set()) -> set:
    """Load safe words from SCOWL while explicitly excluding swear words and their common variants."""
    safe_words = set(COMMON_SAFE_WORDS)
    swear_words = swear_words or set()
    
    filename = "english-words.60"  # Make sure this file exists

    if not os.path.exists(filename):
        print(f"Warning: Safe words list '{filename}' not found! Falling back to COMMON_SAFE_WORDS only.")
        return safe_words
    
    try:
        with open(filename, "r", encoding="ISO-8859-1") as f:  # Use ISO-8859-1 to handle special characters
            for line in f:
                word = line.strip().lower()
                if not word or word in swear_words:
                    continue
                
                is_swear_variant = any(word.startswith(swear) and len(word) - len(swear) <= 3 for swear in swear_words)
                if not is_swear_variant:
                    safe_words.add(word)
    
    except Exception as e:
        print(f"Error loading safe words: {e}")
    
    return safe_words

# ==================== MAIN FILTER CLASS ====================
class SwearFilter:
    def __init__(self, swear_words: set, strict_mode: bool = False):
        self.swear_words = set(word.lower().strip() for word in swear_words)
        self.safe_words = set()  # Already loaded externally if needed
        self.strict_mode = strict_mode
        self.message_cache = {}
        self.cache_max_size = 1000
        self.cache_lock = asyncio.Lock()
   
    def _expand_variants(self, word: str, limit: int = 10000) -> Set[str]:
        from itertools import product

        options = []
        for c in word:
            variants = COMBINED_SUBSTITUTIONS.get(c.lower(), [c.lower()])
            options.append(variants)

        result = set()
        for combo in product(*options):
            result.add(''.join(combo))
            if len(result) >= limit:
                break
        return result

    def _normalize_unicode(self, text: str) -> str:
        normalized = []
        for char in unicodedata.normalize('NFKD', text):
            if not unicodedata.combining(char):
                # Convert to ASCII and lowercase
                c = char.encode('ascii', 'ignore').decode('ascii').lower()
                normalized.append(c if c else '')
        return ''.join(normalized)

    def _simplify_repeats(self, text: str) -> str:
        """Reduce repeated characters to 1 (aaa -> a)"""
        return self.repeat_pattern.sub(r'\1', text)

    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        patterns = {}
        for word in self.swear_words:
            # Base pattern for standard spelling
            base_pattern = re.escape(word)
            
            # Advanced pattern covering:
            # 1. Homoglyphs (𝒮𝒽𝒾𝓉)
            # 2. Leet speak (5h1t)
            # 3. Repeated letters (shiiiiit)
            # 4. Special chars/spaces (s h i t)
            pattern = (
                r'(?:\W|^)(?:'
                rf'[{re.escape(word)}]{{2,}}|'  # Repeated chars
                rf'{base_pattern}|'  # Exact match
                rf'[^\w\s]*{base_pattern}[^\w\s]*|'  # With special chars
                rf'(?:{"|".join(re.escape(c) for c in word)}){{3,}}'  # Split chars
                r')(?:\W|$)'
            )
            patterns[word] = re.compile(pattern, re.IGNORECASE)
        return patterns
    def _compile_all_patterns(self) -> Dict[str, re.Pattern]:
        """Generate regex patterns for all swear words (with variants)"""
        patterns = {}
        for word in self.swear_words:
            pattern_parts = []
            for char in word:
                if char in COMBINED_SUBSTITUTIONS:
                    variants = COMBINED_SUBSTITUTIONS[char]
                    escaped = [re.escape(v) for v in variants]
                    pattern_parts.append(f'[{"".join(escaped)}]')
                else:
                    pattern_parts.append(re.escape(char))
            
            base_pattern = r'[\W_]*'.join(pattern_parts)
            full_pattern = rf'(?<!\w){base_pattern}(?!\w)'
            try:
                patterns[word] = re.compile(full_pattern, re.IGNORECASE)
            except re.error:
                patterns[word] = re.compile(re.escape(word), re.IGNORECASE)
        
        return patterns

    async def _get_cached_result(self, message: str):
        return self.message_cache.get(message)

    async def _cache_message_result(self, message: str, result: bool):
        async with self.cache_lock:
            if len(self.message_cache) >= self.cache_max_size:
                self.message_cache.pop(next(iter(self.message_cache)))
            self.message_cache[message] = result

    def _check_context(self, message: str, word: str) -> bool:
        return False  # Hook for context-aware rules if needed

    def _normalize_text(self, text: str) -> str:
        """Ultimate text normalizer that handles all edge cases while preserving word boundaries"""
        if not text:
            return ""

        # Step 1: Remove hidden characters
        text = remove_hidden_chars(text)
        
        # Step 2: Standard Unicode normalization
        text = unicodedata.normalize('NFKC', text).lower()
        
        # Step 3: Process text word by word to maintain word boundaries
        result = []
        words = re.findall(r'\b[\w\']+\b', text)
        remaining_text = text
        
        for word in words:
            # Find the word in the original text
            word_pos = remaining_text.find(word)
            if word_pos != -1:
                # Add any non-word characters before this word
                result.append(remaining_text[:word_pos])
                remaining_text = remaining_text[word_pos + len(word):]
                
                # Process each character in the word
                normalized_word = []
                for char in word:
                    # Use a consistent normalization map
                    if char in NORMALIZATION_MAP:
                        normalized_word.append(NORMALIZATION_MAP[char])
                    else:
                        normalized_word.append(char)
                
                # Add the normalized word
                norm_word = ''.join(normalized_word)
                # Simplify repeated characters (aaa -> a)
                norm_word = self._simplify_repeats(norm_word)
                result.append(norm_word)
            
        # Add any remaining text
        if remaining_text:
            result.append(remaining_text)
            
        return ''.join(result)

    def _simplify_repeats(self, text: str) -> str:
        """Reduce repeated characters to 1 (aaa -> a)"""
        return self.repeat_pattern.sub(r'\1', text)    
    def debug_normalization(self, text: str) -> dict:
        """Debug helper to show exactly which substitutions are being made"""
        result = {}
        for i, char in enumerate(text):
            if char in NORMALIZATION_MAP and NORMALIZATION_MAP[char] != char:
                result[f"Position {i}"] = {
                    "Original": char,
                    "Normalized": NORMALIZATION_MAP[char],
                    "Unicode point": f"U+{ord(char):04X}"
                }
        return result    
    
    def _check_context(self, message: str, word: str) -> bool:
        """Check if word is in a whitelisted context (e.g., 'classic' vs 'ass')"""
        if word not in CONTEXT_WHITELIST:
            return False
        
        rules = CONTEXT_WHITELIST[word]
        start_time = time.time()
        
        for pattern in rules['patterns']:
            if re.search(pattern, message, re.IGNORECASE):
                return True
            if time.time() - start_time > rules.get('timeout', 2.0):
                break
        
        return False

    def _check_suffix_variations(self, word: str) -> bool:
        """Check for suffixed swears (e.g., 'fucker')"""
        for suffix, rules in SUFFIX_RULES.items():
            if (len(word) >= rules['min_length'] and 
                word.endswith(suffix) and
                word[:-len(suffix)] in self.swear_words and
                word not in rules['exceptions']):
                return True
        
        # Check common prefixes (e.g., 'unfuck')
        common_prefixes = ['re', 'un', 'de', 'in', 'pre', 'pro']
        for prefix in common_prefixes:
            if word.startswith(prefix) and word[len(prefix):] in self.swear_words:
                return True
        
        return False

    def _check_short_swears(self, text: str) -> bool:
        """Detect short swears (e.g., 'wtf', 'fku')"""
        if len(text) <= 3 and text.lower() in SHORT_SWEARS:
            return True
        
        normalized = re.sub(r'[1378245609@#$+*]', '', text.lower())
        return len(normalized) <= 3 and normalized in SHORT_SWEARS

    def _is_english(self, text: str) -> bool:
        """Check if text is English (to reduce false positives)"""
        try:
            return detect(text) == 'en'
        except LangDetectException:
            return True
            
    async def contains_swear_word(self, message: str) -> bool:
        if cached := await self._get_cached_result(message):
            return cached

        if not message or not self.swear_words:
            await self._cache_message_result(message, False)
            return False

        # === RAW token expansion
        words_raw = re.findall(r'\S+', message)
        for word in words_raw:
            variants = expand_all_normalizations(word)
            if any(v in self.swear_words for v in variants):
                await self._cache_message_result(message, True)
                return True

        # === Full normalization
        normalized = preprocess_text_for_filtering(message)
        words_in_message = re.findall(r'\b[\w\']+\b', normalized)

        # === Safe word bypass
        for word in words_in_message:
            if word in self.safe_words and word not in self.swear_words:
                await self._cache_message_result(message, False)
                return False

        # === Direct match
        for word in words_in_message:
            if word in self.swear_words:
                if not self._check_context(message, word):
                    await self._cache_message_result(message, True)
                    return True

        # === Root + suffix match
        for word in words_in_message:
            for swear in self.swear_words:
                if len(swear) < 3: continue
                for i in range(len(word) - len(swear) + 1):
                    segment = word[i:i + len(swear)]
                    variants = expand_all_normalizations(segment)
                    if swear in variants:
                        suffix_len = len(word) - (i + len(swear))
                        if suffix_len <= 3 and not self._check_context(message, word):
                            await self._cache_message_result(message, True)
                            return True

        # === Short-form swears
        if (len(words_in_message) == 1 and
            len(words_in_message[0]) <= 3 and
            words_in_message[0] in SHORT_SWEARS):
            await self._cache_message_result(message, True)
            return True

        # === Phonetic fallback
        phonetic = simple_metaphone(normalized)
        for swear in self.swear_words:
            if simple_metaphone(swear) in phonetic:
                if not self._check_context(message, swear):
                    await self._cache_message_result(message, True)
                    return True

        await self._cache_message_result(message, False)
        return False
    async def _update_cache(self, key: str, value: bool):
        """Thread-safe cache update"""
        async with self.cache_lock:
            if len(self.message_cache) >= self.cache_max_size:
                self.message_cache.pop(next(iter(self.message_cache)))
            self.message_cache[key] = value

    def _normalize_text(self, text: str) -> str:
        """Optimized text normalization"""
        if not text:
            return ""

        # Combine multiple normalization steps
        text = remove_hidden_chars(text)
        text = unicodedata.normalize('NFKC', text).lower()
        text = self._simplify_repeats(text)
        
        # Fast character-by-character normalization
        result = []
        for char in text:
            result.append(NORMALIZATION_MAP.get(char, char))
        return ''.join(result)
    async def test_filter(self, variations: List[str]) -> Dict[str, bool]:
        """Test the filter against a list of variations"""
        return {var: await self.contains_swear_word(var) for var in variations} 
    
if __name__ == "__main__":
    test_words = [
        "fuck", "f@ck", "ƒü¢k", "f*u*c*k", "f.u.c.k", "f u c k", "🅵🆄🅲🅺",
        "shit", "$hit", "sh!t", "s#it", "s.h.i.t", "s h i t",
        "damn", "d@mn", "d@mn!", "D4MN", "d4mn"
    ]

    swear_words = ["fuck", "shit", "damn"]
    sf = SwearFilter(swear_words)

    async def run_tests():
        for msg in test_words:
            result = await sf.contains_swear_word(msg)
            print(f"{msg:15} => {'🚫 BLOCKED' if result else '✅ ALLOWED'}")

    asyncio.run(run_tests())
