"""
Name: utils.py
Description: This file contains useful funtions or classes that are not directly related to any one part of the simulation
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/26/2025 2:19PM PST
"""
import numpy as np

def dict_to_str(dictionary):
    """
    Create a string representation of a dictionary
    """
    # Note: Currently only implemnted for dictionary values that are primitives or lists (not nested dicts)
    str_list = []
    for key, value in dictionary.items():
        if isinstance(value, list):
            str_list.append(f"{key}: {', '.join(value)}")
        elif isinstance(value, (int, float, str, bool, complex, bytes)):
            str_list.append(f"{key}: {value}")
        else:
            raise Exception(
                f"utils.dict_to_str() is not implemented for dictionaries holding this data type ({type(value)})"
            )
    return '\n'.join(str_list)


class ProbabilityStack():
    """
    Defines a class that will hold probability values between 0 and 1 to be referenced during the simulation
    Preallocates for speed and will generate new values when first list runs out
    """

    def __init__(self, length=1000):
        """ Initialize stack """
        self.backing_array = np.random.random(length)
        self.index = 0

    def pop(self):
        """ Get value from array, regenerate if necessary """
        self.index +=1
        try:
            return self.backing_array[self.index-1]
        except IndexError:
            self._regen_stack()
            return self.backing_array[self.index-1]
        
    def _regen_stack(self):
        """ Regenerate the backing array with new probability values """
        self.backing_array = np.random.random(len(self.backing_array))
        self.index=1    