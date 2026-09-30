# Plum Afterglow: compact directory prompt, no command hooks or alias changes.
if [[ $- == *i* && ${TERM:-dumb} != dumb ]]; then
    PS1='\[\e[38;2;190;176;187m\]\w\[\e[0m\] \[\e[38;2;213;161;170m\]❯\[\e[0m\] '
fi
