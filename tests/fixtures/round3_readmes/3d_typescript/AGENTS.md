Your job is to port Aspose.3D for Python FOSS (located in directory `foss-python`) to TypeScript.


## Naming conventions

Naming should follow the original Aspose.3D for Python, and obey the standard conventions of TypeScript.


## Directory Structures

The original Python version is located in directory `foss-python`
The TypeScript source code is located in directory `src`
Test files are copied from the Python FOSS version and located in directory `foss-python/tests/`



### Rules
- Do not invent types/members that are not defined in original Python version
- Keep the same type hierarchy from the original Python version.
- Every commit should record the original commit hash from `foss-python` directory for tracking upstream changes
- Test rules follow the original version.

### Additional notes

- Python decorators like `@property` and `@classmethod` have direct equivalents in TypeScript (getters/setters and `static` methods), so no special handling is needed
- Module/package structure should follow standard TypeScript conventions (using ES modules and `.ts` files)
- Build and test follow standard TypeScript/Node.js practices (using `npm` scripts, Jest/Mocha for testing, etc.) 
