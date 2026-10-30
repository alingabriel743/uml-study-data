# File: app/utils/ir_extraction/base_extractor.py
from abc import ABC, abstractmethod

class BaseIRExtractor(ABC):
    """
    Base abstract class for all IR extractors.
    Each language-specific extractor should extend this class.
    """
    
    def __init__(self, repo_path):
        """
        Initialize the extractor with the repository path.
        
        Args:
            repo_path (str): Path to the cloned repository
        """
        self.repo_path = repo_path
        self.ir = self._initialize_ir()
    
    def _initialize_ir(self):
        """
        Initialize the base IR structure.
        
        Returns:
            dict: The empty IR structure
        """
        return {
            "metadata": {
                "projectName": self.repo_path.split('/')[-1],
                "sourceLanguages": [],
                "sourceFiles": []
            }
        }
    
    @abstractmethod
    def extract_ir(self):
        """
        Extract the Intermediate Representation from the repository.
        
        Returns:
            dict: The IR structure populated with extracted information
        """
        pass
    
    def save_ir(self, output_path):
        """
        Save the IR to a JSON file.
        
        Args:
            output_path (str): Path to save the IR JSON file
            
        Returns:
            str: The path to the saved file
        """
        import json
        import os
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(self.ir, f, indent=2)
        
        return output_path